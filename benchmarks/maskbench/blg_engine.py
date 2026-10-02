"""Bolorgir engine for MaskBench.

Bolorgir is a structured-generation engine whose core is a Zig library
exposed through a C ABI; the `bolorgir` Python package is the official
binding (pip install bolorgir).

Engine notes:

* The JSON Schema profile ("canonical-v1") is a strict subset of JSON:
  every object value declares `properties`, `required` and
  `additionalProperties: false` explicitly, and serialization is compact
  (no whitespace outside strings). Run MaskBench with `--compact` so
  instances are serialized in the compact form; without it, spaced
  instances are rejected at the first whitespace token and only the
  compile statistics are meaningful.
* `compile_grammar` maps to `Engine.compile`, `reset` to a new session
  of the compiled grammar, `compute_mask` to `Session.fill_mask`,
  `commit_token` to a mask bit test followed by `Session.accept_token`.
* The token table (~vocab size, bytes per token) is built once in
  `init`, so it is not part of `ttfm_us`.

Profile selection (canonical-v1 / spec-v1):

* The compile profile is explicit: `canonical-v1` (frozen default) or
  `spec-v1` (the full JSON Schema profile; implemented in current engine
  builds, support table in docs/supported_features.md section 1a,
  normative semantics in docs/semantics-spec-v1.md). Selection, in
  priority order: the `BlgEngine(profile=...)` constructor argument,
  then the `BOLORGIR_PROFILE` environment variable, then the default
  `canonical-v1`. With the stock MaskBench runner the env var is
  the CLI knob: `BOLORGIR_PROFILE=spec-v1 python3 scripts/run_maskbench.py
  --blg ...`. An unknown name raises immediately (a typo must not silently
  demote a run).
* The knob passes straight through to `Engine.compile(..., profile=...)`
  (`blg_compile_request.profile` in the C ABI): canonical-v1 is the
  default, spec-v1 dispatches to the spec-v1 kernel path. A build that
  does not implement a requested profile rejects the compile with
  BLG_ERR_UNSUPPORTED_FEATURE.
* Compatibility fallback: a requested profile that the installed engine
  build does not implement (e.g. spec-v1 on a pre-spec-v1 build) falls
  back to canonical-v1. The probe (`_engine_supports_profile`) compiles
  a trivial schema on a synthetic byte tokenizer once per process,
  outside every measured section; the fallback is reported on stderr and
  visible in `requested_profile` vs `profile`. On current builds the
  probe passes and the requested profile is the effective one.
* Recording (A8.4): the effective profile is carried by the engine id -
  a non-default profile renames the run to `blg-<profile>`, hence the
  output directory `tmp/out--blg-<profile>` and the results-table row.
  The upstream per-file status JSONs are written by the harness and
  carry no engine metadata; with the default profile the id stays `blg`,
  so canonical-v1 runs are bit-for-bit identical to before.

Value-preserving serializer (semantics-spec-v1 section 6, opt-in):

* The spec-v1 byte language fixes the declared key order by the schema,
  while `--compact` keeps the data-determined order (section 5), so
  spec-v1 runs route instances through `bolorgir.serializer`
  (`serialize_value`): declared keys are reordered to the schema
  declaration order, values are preserved exactly, output is the
  compact escape table form. The runner hands the hook the already
  parsed `test["data"]`, so the value entry point is used: a string
  instance is a JSON string value and is never re-parsed as JSON text
  ("1" stays the string "1"). Selection, in priority order: the
  `BlgEngine(serializer=...)` constructor argument, then the
  `BOLORGIR_SERIALIZER=1` environment variable; default OFF, so
  recorded runs stay comparable bit-for-bit.
* The runner calls the engine's `serialize_instance` hook when it
  exists; None means "use the standard serialization" (serializer off).
  A refusal raises an exception with `serialization_incompatible =
  True`, which the runner counts in its own per-file bucket
  (`num_serialization_incompatible`, surfaced as the "serialization
  incompat" results row) - a protocol artifact, never a validation
  error (section 7).
* An enabled serializer renames the run id with a `-ser` suffix, so
  MaskBench artifacts record the protocol they measured.
"""

import os

from .engine import Engine

import bolorgir as blg

DEFAULT_PROFILE = "canonical-v1"
SPEC_PROFILE = "spec-v1"
KNOWN_PROFILES = (DEFAULT_PROFILE, SPEC_PROFILE)
PROFILE_ENV_VAR = "BOLORGIR_PROFILE"
SERIALIZER_ENV_VAR = "BOLORGIR_SERIALIZER"

# Per-process probe results: profile name -> implemented by this build.
_profile_support = {}


def _engine_supports_profile(profile: str) -> bool:
    """Capability probe: does this engine build implement the profile?

    Compiles a trivial schema under the profile on a synthetic byte
    tokenizer. The kernel checks the profile before reading the schema
    ("unsupported profile" -> BLG_ERR_UNSUPPORTED_FEATURE), so
    UnsupportedFeatureError here means the profile is not implemented;
    anything schema- or tokenizer-related would fail under canonical-v1
    too and propagates.
    """
    supported = _profile_support.get(profile)
    if supported is None:
        bundle = blg.TokenizerBundle.from_token_bytes(
            [bytes([b]) for b in range(256)]
        )
        probe = blg.Engine(mode="lazy", tokenizer=bundle)
        try:
            constraint = probe.compile({"type": "integer"}, profile=profile)
            constraint.close()
            supported = True
        except blg.UnsupportedFeatureError:
            supported = False
        finally:
            probe.close()
        _profile_support[profile] = supported
    return supported


class BlgEngine(Engine):

    def __init__(self, profile: str = None, serializer: bool = None):
        super().__init__()
        self.engine = None
        self.bundle = None
        self.constraint = None
        self.session = None
        self.mask = b""
        self.schema = None
        requested = profile or os.environ.get(PROFILE_ENV_VAR) or DEFAULT_PROFILE
        if requested not in KNOWN_PROFILES:
            raise ValueError(
                f"unknown bolorgir profile {requested!r}; "
                f"expected one of: {list(KNOWN_PROFILES)}"
            )
        self.requested_profile = requested
        # Capability check: an engine build without the requested profile
        # (e.g. spec-v1 on a pre-spec-v1 build) falls back to canonical-v1;
        # the effective profile is what gets recorded.
        if requested != DEFAULT_PROFILE and not _engine_supports_profile(requested):
            self.profile = DEFAULT_PROFILE
        else:
            self.profile = requested
        # Value-preserving serializer (spec-v1 section 6): opt-in, default
        # off so recorded runs stay comparable.
        if serializer is None:
            serializer = os.environ.get(SERIALIZER_ENV_VAR, "") == "1"
        self.serializer = serializer

    def get_id(self):
        # The id names the output directory (tmp/out--<id>) and the row in
        # the results table, so it records the effective profile (A8.4)
        # and the serializer protocol.
        id = "blg" if self.profile == DEFAULT_PROFILE else f"blg-{self.profile}"
        if self.serializer:
            id += "-ser"
        return id

    def get_name(self):
        return "Bolorgir"

    def get_module(self):
        return "bolorgir"

    def get_version(self):
        # The package may be used from a source tree (PYTHONPATH) where
        # importlib.metadata has no distribution to look up.
        import importlib.metadata

        try:
            return importlib.metadata.version("bolorgir")
        except importlib.metadata.PackageNotFoundError:
            return blg.__version__

    def init(self):
        # The token table and context are built here (outside ttfm_us):
        # compile_grammar then measures grammar compilation only, matching
        # engines whose compiler object is constructed before the first
        # compile call.
        self.bundle = blg.TokenizerBundle.from_hf(self.tokenizer)
        self.engine = blg.Engine(
            mode="adaptive", memory_limit_mb=256, tokenizer=self.bundle
        )
        if self.requested_profile != self.profile:
            self.log_single(
                f"bolorgir profile {self.requested_profile!r} is not "
                f"implemented in bolorgir {self.get_version()}; "
                f"falling back to {self.profile!r}"
            )

    def compile_grammar(self, schema: dict):
        self.__drop_session()
        if self.constraint is not None:
            self.constraint.close()
            self.constraint = None
        self.schema = schema
        self.constraint = self.engine.compile(
            schema, self.bundle, profile=self.profile
        )

    def serialize_instance(self, data):
        """Runner hook (spec-v1 section 6): the instance through the
        value-preserving serializer.

        `data` is the runner's already-parsed `test["data"]`, so it
        goes through serialize_value: a string is a JSON string value
        and is never re-parsed as JSON text. Returns None when the
        serializer is off - the runner then uses its standard
        serialization (default behavior, bit-for-bit). A refusal raises
        SerializationIncompatibleError, which carries
        `serialization_incompatible = True` for the runner's separate
        bucket (section 7). bolorgir.serializer is imported lazily so
        the adapter still loads against a bolorgir without the module.
        """
        if not self.serializer:
            return None
        from bolorgir.serializer import (
            SerializationIncompatibleError,
            serialize_value,
        )

        payload, status = serialize_value(data, self.schema)
        if not status.ok:
            raise SerializationIncompatibleError(status.reason, status.pointer)
        return payload.decode("utf-8")

    def reset(self):
        self.__drop_session()
        self.session = self.constraint.create_session()
        self.mask = b""

    def compute_mask(self):
        self.mask = self.session.fill_mask()

    def commit_token(self, t: int) -> bool:
        if t >> 3 >= len(self.mask):
            return False
        if not (self.mask[t >> 3] & (1 << (t & 7))):
            return False
        self.session.accept_token(t)
        return True

    def __drop_session(self):
        if self.session is not None:
            self.session.close()
            self.session = None
