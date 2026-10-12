# vLLM integration

This directory holds the reproducible integration of the Bolorgir
structured-output backend with vLLM. The backend implementation lives in
`python/bolorgir/vllm.py`; the patch here registers it in vLLM.

## Pinned stack

| Component | Pin | Identity |
|---|---|---|
| vLLM | tag `v0.31.0` | git commit `db9527a46873454610df6dbedf79a36d6bf1a7f6` |

vLLM 0.31.0 has no registration point for a new structured-output
backend: the backend name is a closed `Literal` in
`vllm/config/structured_outputs.py`, backend instances are created in a
fixed `if`/`elif` chain in
`vllm/v1/structured_output/__init__.py`, and request validation selects a
backend in `vllm/sampling_params.py`. Installing `bolorgir[vllm]` alone
therefore does not enable the backend; the patch below adds the name in
these three places and imports the backend class from the installed
`bolorgir` package. The same change is the material for an upstream PR
(D3); until it is merged, this is a source patch against the pinned tag,
not support for an unmodified vLLM.

## Apply

```bash
git clone --branch v0.31.0 --depth 1 https://github.com/vllm-project/vllm.git
cd vllm
git apply /path/to/bolorgir/integrations/vllm/vllm-0.31.0-bolorgir.patch
pip install -e .          # or the build command of your platform
pip install "bolorgir[vllm]"
```

Patch identity (sha256):
`38c0272a5cea02fd040258e85d2459c6a2c97acb2668c2155fa33c943b8cbf2b`.

Revert with `git apply -R ...` or by checking out the three files.

## Run

```bash
vllm serve Qwen/Qwen2.5-0.5B-Instruct \
    --structured-outputs-config '{"backend": "bolorgir"}'
```

Requests then carry the usual OpenAI-compatible fields
(`response_format={"type": "json_schema", ...}`, `structured_outputs.regex`,
`structured_outputs.choice`); see `docs/integrations/vllm.md` for the full
request matrix, supported and refused formats, and limits. The server log
line `bolorgir structured-output backend active: ...` names the extension
file, tokenizer and vocabulary actually in use; a request whose format the
backend refuses fails with a clear error and never falls back to another
backend.

## Verification status

- The patch applies cleanly to the pinned tag (checked with
  `git apply --check`).
- The backend module is exercised against a faithful stand-in of the
  vLLM interface in `tests/test_vllm_backend.py` (no GPU needed); with the
  environment variable `BLG_VLLM_SRC` pointing at the pinned source tree,
  the same test file verifies the adapter's interface against the actual
  `backend_types.py` of the tag.
- End-to-end serving checks on GPU (server start, request matrix,
  speculative decoding on/off) require the vLLM stack and are recorded
  separately; a skipped GPU check means "not verified", not "passed".
