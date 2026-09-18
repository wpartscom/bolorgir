#!/usr/bin/env python3
"""T2: побитовый паритет масок zig-constraints vs xgrammar и llguidance.

Пересечение языков: схемы JSONSchemaBench, скомпилировавшиеся нашим движком
(исход — support_report.json), ∩ компилируемые xgrammar ∩ компилируемые
llguidance, плюс весь контрольный корпус benchmarks/corpus (json_schema с
expect_support=true).

Метод: детерминированные валидные трассы токенов gpt2 (seed закреплён),
генерируемые нашим движком (трасса в языке canonical-v1 ⊂ языка конкурентов).
На каждом префиксе сравниваются маски разрешённых токенов трёх движков
бит-к-биту (без бита EOS; завершение сравнивается отдельным флагом).
Параллельно с трассой идёт независимый oracle tests/reference.py (Matcher),
к которому скармливаются те же байты.

Классификация расхождений (ТЗ T2: «разница в профилях сначала
классифицируется»):
  expected:whitespace          — токен из одних JSON-пробелов;
  expected:whitespace-mixed    — токен с пробельными байтами, oracle подтверждает
                                 «вне canonical-v1» (без rollout-проверки);
  expected:profile             — oracle: вне canonical-v1; rollout конкурента
                                 завершился документом, валидным по исходной
                                 схеме (jsonschema) — чистая разница профилей;
  expected:profile-unverified  — oracle: вне canonical-v1, rollout не выполнялся
                                 (бюджет), валидность завершения не проверена;
  expected:llguidance-ff-approximation — битмаска llguidance неполна
                                 (fast-forward схема): validate_tokens токен
                                 принимает; артефакт их mask-интерфейса;
  competitor-overgeneration    — rollout конкурента дал документ, невалидный по
                                 исходной схеме (звучность конкурента);
  competitor-undergeneration   — наша маска разрешила токен, oracle подтверждает
                                 каноничность префикса, конкурент запретил
                                 (сужение языка конкурентом);
  competitor-mask-overapproximation — битмаска llguidance разрешила токен,
                                 который validate_tokens отклоняет;
  competitor-accept-failure    — конкурент отклонил accept токена трассы;
  REAL-BUG:ours-forbids-canonical   — oracle: prefix+token ∈ canonical-v1, а наша
                                 маска токен запретила (полнота);
  REAL-BUG:ours-allows-invalid — наша маска разрешила токен, rollout завершился
                                 документом вне схемы/не-JSON (звучность);
  REAL-BUG:ours-allows-noncanonical — oracle: prefix+token вне canonical-v1, а
                                 наша маска разрешила (ожидает rollout-подтверждения);
  REAL-BUG:ours-accept-failure — наш accept отклонил токен из собственной маски;
  REAL-BUG:ours-trace-noncanonical — наша трасса ушла из языка canonical-v1
                                 (oracle не принял байты);
  inconclusive                 — rollout не завершился / префикс отклонён;
  unclassified                 — бюджет классификации исчерпан, oracle недоступен.

Для каждого расхождения сохраняются схема, профиль, tokenizer revision,
token IDs префикса и различающиеся биты (ТЗ T2). REAL-BUG* дополнительно
пишутся reproducer-файлами в --reproducers-dir.

Правило выборки JSB: все схемы с outcome=compiled из support_report, но не
более --per-dataset на датасет (детерминированно: random.Random(seed).sample
по отсортированному списку имён датасета).
"""

import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), os.pardir, "tests"))

import bench_common as bc  # noqa: E402

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"
PROFILE = "canonical-v1"
WS_BYTES = set(b" \t\n\r")


class Engines:
    """Три движка + oracle, общие на весь прогон."""

    def __init__(self):
        import numpy as np
        import xgrammar as xg
        import llguidance as lg
        import llguidance.hf as lghf
        from transformers import AutoTokenizer

        import zig_constraints as zc
        import reference

        self.np = np
        self.zc = zc
        self.xg = xg
        self.lg = lg
        self.reference = reference

        hf = AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                           revision=TOKENIZER_REVISION)
        self.bundle = zc.TokenizerBundle.from_hf(hf)
        self.vocab_size = self.bundle.vocab_size
        self.eos_ids = set(self.bundle.eos_ids)
        self.engine = zc.Engine(mode="adaptive", tokenizer=self.bundle)
        self.xg_info = xg.TokenizerInfo.from_huggingface(hf)
        # cache_enabled=False: в xgrammar 0.2.6 GrammarCompiler с кэшем
        # наблюдалось перекрёстное загрязнение грамматик между схемами
        # (см. parity_summary.md: eos-policy-divergence на o21459 после
        # o10014/o13837/o21458; воспроизводится только с общим кэшем)
        self.xg_compiler = xg.GrammarCompiler(self.xg_info,
                                              cache_enabled=False)
        self.lg_tok = lghf.from_tokenizer(hf)
        assert self.xg_info.vocab_size == self.vocab_size
        assert self.lg_tok.vocab_size == self.vocab_size

    def bits_from_bytes(self, mask_bytes):
        a = self.np.frombuffer(mask_bytes, dtype=self.np.uint8)
        return self.np.unpackbits(a, bitorder="little")[: self.vocab_size]

    def bits_from_xg(self, bitmask):
        arr = bitmask.numpy().astype(self.np.uint32, copy=False).ravel()
        return self.np.unpackbits(arr.view(self.np.uint8),
                                  bitorder="little")[: self.vocab_size]

    def token_text(self, tid):
        return self.bundle.token_bytes(tid)


class CompetitorState:
    """Живое состояние внешнего движка на трассе."""

    def __init__(self, kind):
        self.kind = kind
        self.matcher = None
        self.dead = False

    def start(self):
        raise NotImplementedError

    def allowed(self):
        """-> (bits ndarray | None, terminated bool)."""
        raise NotImplementedError

    def accept(self, tid):
        raise NotImplementedError


class XgState(CompetitorState):
    def __init__(self, env, compiled):
        super().__init__("xgrammar")
        self.env = env
        self.compiled = compiled

    def start(self):
        self.matcher = self.env.xg.GrammarMatcher(self.compiled)
        self.bitmask = self.env.xg.allocate_token_bitmask(
            1, self.env.xg_info.vocab_size)
        self.dead = False

    def allowed(self):
        if self.matcher.is_terminated():
            return None, True
        self.matcher.fill_next_token_bitmask(self.bitmask)
        return self.env.bits_from_xg(self.bitmask), False

    def accept(self, tid):
        return bool(self.matcher.accept_token(tid))


class LgState(CompetitorState):
    def __init__(self, env, grammar):
        super().__init__("llguidance")
        self.env = env
        self.grammar = grammar

    def start(self):
        self.matcher = self.env.lg.LLMatcher(self.env.lg_tok, self.grammar)
        self.dead = False

    def allowed(self):
        if self.matcher.is_stopped():
            return None, True
        bm = self.matcher.compute_bitmask()
        if bm is None:
            return None, self.matcher.is_stopped()
        return self.env.bits_from_bytes(bm), False

    def accept(self, tid):
        ok = self.matcher.consume_token(tid)
        return bool(ok) and not self.matcher.is_error()

    def probe(self, tid):
        """Принял бы движок tid сейчас (без изменения живого состояния)."""
        cp = self.matcher.deep_copy()
        try:
            return cp.validate_tokens([tid]) == 1 and not cp.is_error()
        except Exception:  # noqa: BLE001
            return False


def _rollout_competitor(env, make_state, prefix_ids, tid, cap=400):
    """Завершение конкурента через prefix+[tid].

    -> (final_bytes | None, note). Использует НОВЫЙ matcher, живое состояние
    трассы не трогает. Документ считается завершённым, когда движок сигналит
    terminated ИЛИ когда маска пуста за вычетом EOS (EOS-подтверждение
    завершения; xgrammar/llguidance переводят флаг terminated только после
    фактического consume EOS). До трёх попыток: первая — жадная по наименьшему
    id, дальнейшие — случайные (незамкнутые строки жадной стратегией не
    закрываются).
    """
    rng = random.Random(f"rollout:{tid}:{prefix_ids[:8]}")
    for attempt in range(3):
        st = make_state()
        for t in prefix_ids + [tid]:
            if not st.accept(t):
                return None, "конкурент отклонил префикс при rollout"
        ids = list(prefix_ids) + [tid]
        for _ in range(cap):
            bits, terminated = st.allowed()
            if terminated:
                return b"".join(env.token_text(i) for i in ids), ""
            if bits is None:
                break
            cand = [i for i in bits.nonzero()[0].tolist()
                    if i not in env.eos_ids]
            if not cand:
                if any(bits[e] for e in env.eos_ids):
                    return (b"".join(env.token_text(i) for i in ids),
                            "завершено по EOS в маске")
                break
            t = cand[0] if attempt == 0 else cand[rng.randrange(len(cand))]
            if not st.accept(t):
                break
            ids.append(t)
    return None, f"rollout не завершился за {cap} шагов"


def _rollout_ours(env, schema_str, prefix_ids, tid, cap=400):
    """Greedy-завершение нашего движка через prefix+[tid] (новая сессия)."""
    constraint = env.engine.compile(schema_str.encode("utf-8"))
    session = constraint.create_session()
    try:
        ids = list(prefix_ids) + [tid]
        for t in ids:
            session.accept_token(t)
        for _ in range(cap):
            if session.can_end():
                return b"".join(env.token_text(i) for i in ids), ""
            allowed = [i for i in session.allowed_token_ids()
                       if i not in env.eos_ids]
            if not allowed:
                return None, "пустая маска до can_end (DeadEnd?)"
            session.accept_token(allowed[0])
            ids.append(allowed[0])
        return None, f"rollout не завершился за {cap} шагов"
    finally:
        session.close()


def _jsonschema_valid(env, final_bytes, schema_obj):
    """-> (ok, detail)."""
    import jsonschema
    try:
        doc = json.loads(final_bytes.decode("utf-8"))
    except Exception:
        return False, f"невалидный JSON: {final_bytes[:100]!r}"
    try:
        jsonschema.validate(doc, schema_obj)
    except Exception as e:
        return False, f"jsonschema: {str(e)[:150]}; doc={final_bytes[:100]!r}"
    return True, ""


def classify_divergence(env, direction, eng_name, live_state, make_state,
                        ref_matcher, schema_str, schema_obj, prefix_ids, tid,
                        rollout_allowed):
    """Классификация одного расхождения. -> (class, detail).

    ref_matcher — oracle, скормленный байтам prefix (или None).
    live_state — живое состояние конкурента на трассе (для probe).
    rollout_allowed — остался бюджет rollout-проверок.
    """
    tok_bytes = env.token_text(tid)
    if set(tok_bytes) <= WS_BYTES:
        return "expected:whitespace", ""
    canonical = None
    if ref_matcher is not None:
        probe = ref_matcher.clone()
        canonical = bool(probe.feed(tok_bytes))

    if direction == "competitor-allows":
        if eng_name == "llguidance" and live_state is not None \
                and not live_state.probe(tid):
            return ("competitor-mask-overapproximation",
                    "битмаска llguidance разрешила токен, validate отклонил")
        if canonical is True:
            return ("REAL-BUG:ours-forbids-canonical",
                    "oracle: prefix+token ∈ canonical-v1, наша маска запретила")
        if rollout_allowed:
            final, note = _rollout_competitor(env, make_state, prefix_ids, tid)
            if final is None:
                return "inconclusive", note
            ok, detail = _jsonschema_valid(env, final, schema_obj)
            if not ok:
                return "competitor-overgeneration", detail
            return "expected:profile", f"валидно, вне canonical-v1: {final[:120]!r}"
        if set(tok_bytes) & WS_BYTES:
            return "expected:whitespace-mixed", "oracle: вне canonical-v1"
        if canonical is False:
            return ("expected:profile-unverified",
                    "oracle: вне canonical-v1; rollout не выполнялся")
        return "unclassified", "oracle недоступен, бюджет исчерпан"

    # direction == "ours-allows"
    if eng_name == "llguidance" and live_state is not None \
            and live_state.probe(tid):
        # битмаска llguidance — fast-forward аппроксимация: validate_tokens
        # токен принимает, расхождение — артефакт их mask-интерфейса
        return ("expected:llguidance-ff-approximation",
                "validate_tokens принимает; битмаска неполна (ff-схема)")
    if canonical is True:
        return ("competitor-undergeneration",
                "oracle: prefix+token ∈ canonical-v1; конкурент запретил")
    if canonical is False:
        if rollout_allowed:
            final, note = _rollout_ours(env, schema_str, prefix_ids, tid)
            if final is None:
                return ("REAL-BUG:ours-allows-noncanonical",
                        f"oracle: вне canonical-v1; rollout: {note}")
            ok, detail = _jsonschema_valid(env, final, schema_obj)
            if not ok:
                return "REAL-BUG:ours-allows-invalid", detail
            return ("REAL-BUG:ours-allows-noncanonical",
                    f"oracle: вне canonical-v1, doc валиден по схеме: "
                    f"{final[:120]!r}")
        return ("REAL-BUG:ours-allows-noncanonical",
                "oracle: вне canonical-v1; rollout не выполнялся")
    # oracle недоступен
    if rollout_allowed:
        final, note = _rollout_ours(env, schema_str, prefix_ids, tid)
        if final is None:
            return "inconclusive", note
        ok, detail = _jsonschema_valid(env, final, schema_obj)
        if not ok:
            return "REAL-BUG:ours-allows-invalid", detail
        return "competitor-undergeneration", f"валидный doc: {final[:120]!r}"
    return "unclassified", "oracle недоступен, бюджет исчерпан"


def run_schema_parity(env, schema_str, schema_obj, ref_lang, xg_compiled,
                      lg_grammar, n_traces, max_steps, classify_budget, seed,
                      rec_cap=200):
    """Паритет по трассам одной схемы. -> dict результата."""
    states = {}
    if xg_compiled is not None:
        states["xgrammar"] = XgState(env, xg_compiled)
    if lg_grammar is not None:
        states["llguidance"] = LgState(env, lg_grammar)
    make_state = {
        "xgrammar": lambda: _started(XgState(env, xg_compiled)),
        "llguidance": lambda: _started(LgState(env, lg_grammar)),
    }
    res = {
        "prefixes": 0,
        "raw_exact": {k: 0 for k in states},
        "termination_mismatch": {k: 0 for k in states},
        "divergence_classes": {k: {} for k in states},
        "divergences": {k: [] for k in states},
        "divergence_total": {k: 0 for k in states},
        "classify_budget_used": {k: 0 for k in states},
        "traces_completed": 0,
    }
    constraint = env.engine.compile(schema_str.encode("utf-8"))

    for trace_idx in range(n_traces):
        rng = random.Random(f"{seed}:{trace_idx}:{schema_str[:64]}")
        session = constraint.create_session()
        for st in states.values():
            st.start()
        ref_matcher = (env.reference.Matcher(ref_lang)
                       if ref_lang is not None else None)
        prefix_ids = []
        for step in range(max_steps):
            our_bits = env.bits_from_bytes(session.fill_mask())
            our_end = session.can_end()
            for name, st in states.items():
                if st.dead:
                    continue
                try:
                    bits, terminated = st.allowed()
                except Exception as e:  # noqa: BLE001
                    st.dead = True
                    _record(env, res, name, prefix_ids, None, "engine-error",
                            f"{type(e).__name__}: {e}")
                    continue
                if terminated != our_end:
                    res["termination_mismatch"][name] += 1
                if bits is None:
                    continue
                our_eos = any(our_bits[e] for e in env.eos_ids)
                cmp_eos = any(bits[e] for e in env.eos_ids)
                if our_eos != cmp_eos:
                    _record(env, res, name, prefix_ids, None,
                            "eos-policy-divergence",
                            f"наш EOS-бит={our_eos}, конкурента={cmp_eos}",
                            None, rec_cap)
                cmp_bits = our_bits.copy()
                for e in env.eos_ids:
                    cmp_bits[e] = 0
                    bits[e] = 0
                diff = cmp_bits != bits
                if not diff.any():
                    res["raw_exact"][name] += 1
                    continue
                for tid in diff.nonzero()[0].tolist():
                    direction = ("ours-allows" if cmp_bits[tid]
                                 else "competitor-allows")
                    budget = (res["classify_budget_used"][name]
                              < classify_budget)
                    if budget:
                        res["classify_budget_used"][name] += 1
                    cls, detail = classify_divergence(
                        env, direction, name, st, make_state[name],
                        ref_matcher, schema_str, schema_obj, prefix_ids, tid,
                        budget)
                    _record(env, res, name, prefix_ids, tid, cls, detail,
                            direction, rec_cap)
            res["prefixes"] += 1
            allowed = [i for i in our_bits.nonzero()[0].tolist()
                       if i not in env.eos_ids]
            if not allowed or (our_end and rng.random() < 0.75):
                break
            tid = allowed[rng.randrange(len(allowed))]
            try:
                session.accept_token(tid)
            except Exception as e:  # noqa: BLE001 — токен из своей маски!
                for name in states:
                    _record(env, res, name, prefix_ids, tid,
                            "REAL-BUG:ours-accept-failure", str(e),
                            "ours-allows", rec_cap)
                break
            prefix_ids.append(tid)
            if ref_matcher is not None:
                if not ref_matcher.feed(env.token_text(tid)):
                    for name in states:
                        _record(env, res, name, prefix_ids, tid,
                                "REAL-BUG:ours-trace-noncanonical",
                                "oracle не принял байты нашей трассы",
                                "ours-allows", rec_cap)
                    ref_matcher = None  # oracle состояние мёртво
            for st in states.values():
                if st.dead:
                    continue
                try:
                    if not st.accept(tid):
                        st.dead = True
                        _record(env, res, st.kind, prefix_ids, tid,
                                "competitor-accept-failure",
                                "токен из маски трассы отклонён", "ours-allows",
                                rec_cap)
                except Exception as e:  # noqa: BLE001
                    st.dead = True
                    _record(env, res, st.kind, prefix_ids, tid, "engine-error",
                            f"{type(e).__name__}: {e}", None, rec_cap)
        if session.can_end():
            res["traces_completed"] += 1
        session.close()
    return res


def _started(st):
    st.start()
    return st


def _record(env, res, name, prefix_ids, tid, cls, detail, direction=None,
            rec_cap=200):
    res["divergence_total"][name] += 1
    cc = res["divergence_classes"][name]
    cc[cls] = cc.get(cls, 0) + 1
    if len(res["divergences"][name]) < rec_cap:
        rec = {
            "prefix_token_ids": list(prefix_ids),
            "class": cls,
            "detail": detail[:300],
            "tokenizer": {"name": TOKENIZER_NAME,
                          "revision": TOKENIZER_REVISION},
            "profile": PROFILE,
        }
        if tid is not None:
            rec["token_id"] = tid
            rec["token_bytes"] = repr(env.token_text(tid))
            rec["direction"] = direction
        res["divergences"][name].append(rec)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--support-report", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reproducers-dir", required=True)
    ap.add_argument("--per-dataset", type=int, default=20)
    ap.add_argument("--traces", type=int, default=2)
    ap.add_argument("--max-steps", type=int, default=200)
    ap.add_argument("--classify-budget", type=int, default=8)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    zc = bc.require_core()
    for pkg in ("xgrammar", "llguidance", "transformers", "numpy",
                "jsonschema"):
        if bc.import_or_skip(pkg) is None:
            bc.skip(f"{pkg} не установлен")

    with open(args.support_report, encoding="utf-8") as f:
        support = json.load(f)

    # --- выборка JSB ---
    rng = random.Random(args.seed)
    by_dataset = {}
    for p in support["per_schema"]:
        if p["outcome"] == "compiled":
            by_dataset.setdefault(p["dataset"], []).append(p["name"])
    data_dir = os.path.join(os.path.dirname(_HERE), "external",
                            "jsonschemabench", "data")
    selected = []
    sampling_notes = []
    for dataset in sorted(by_dataset):
        names = sorted(by_dataset[dataset])
        if len(names) <= args.per_dataset:
            take = names
            sampling_notes.append(f"{dataset}: все {len(names)}")
        else:
            take = sorted(rng.sample(names, args.per_dataset))
            sampling_notes.append(
                f"{dataset}: {args.per_dataset} из {len(names)} "
                f"(Random({args.seed}).sample по отсортированным именам)")
        for name in take:
            with open(os.path.join(data_dir, dataset, name + ".json"),
                      encoding="utf-8") as f:
                selected.append({"source": "jsonschemabench",
                                 "dataset": dataset, "name": name,
                                 "schema_str": f.read()})

    # --- контрольный корпус ---
    n_control = 0
    for name, kind, expect_support, expect_error, raw in bc.load_corpus():
        if kind != "json_schema" or not expect_support:
            continue
        selected.append({"source": "control_corpus", "dataset": "control",
                         "name": name, "schema_str": raw.decode("utf-8")})
        n_control += 1
    sampling_notes.append(f"control: все {n_control} json_schema с "
                          "expect_support=true")

    env = Engines()
    os.makedirs(args.reproducers_dir, exist_ok=True)

    per_schema = []
    repro_idx = 0
    t_start = time.time()
    for idx, entry in enumerate(selected):
        schema_str = entry["schema_str"]
        sres = {"source": entry["source"], "dataset": entry["dataset"],
                "name": entry["name"]}
        try:
            schema_obj = json.loads(schema_str)
        except Exception as e:
            sres.update(status="harness-error", detail=f"json: {e}")
            per_schema.append(sres)
            continue
        # наш движок
        try:
            env.engine.compile(schema_str.encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            sres.update(status="our-compile-error",
                        detail=f"{type(e).__name__}: {e}")
            per_schema.append(sres)
            continue
        # oracle
        ref_lang = None
        ref_status = "ok"
        try:
            ref_lang = env.reference.compile_schema_text(schema_str)
        except Exception as e:  # noqa: BLE001
            ref_status = f"{type(e).__name__}: {e}"
        sres["reference_oracle"] = ref_status
        # xgrammar
        try:
            xg_compiled = env.xg_compiler.compile_json_schema(schema_str)
            sres["xgrammar"] = {"status": "compiled"}
        except Exception as e:  # noqa: BLE001
            sres["xgrammar"] = {"status": "compile-error",
                                "detail": f"{type(e).__name__}: {str(e)[:200]}"}
            xg_compiled = None
        # llguidance
        try:
            lg_grammar = env.lg.grammar_from("json_schema", schema_str)
            sres["llguidance"] = {"status": "compiled"}
        except Exception as e:  # noqa: BLE001
            sres["llguidance"] = {"status": "compile-error",
                                  "detail": f"{type(e).__name__}: {str(e)[:200]}"}
            lg_grammar = None
        if xg_compiled is None and lg_grammar is None:
            sres["status"] = "no-intersection"
            per_schema.append(sres)
            continue
        try:
            parity = run_schema_parity(
                env, schema_str, schema_obj, ref_lang, xg_compiled, lg_grammar,
                args.traces, args.max_steps, args.classify_budget, args.seed)
        except Exception as e:  # noqa: BLE001 — одна схема не роняет прогон
            import traceback
            sres.update(status="parity-error",
                        detail=f"{type(e).__name__}: {e}",
                        traceback=traceback.format_exc()[-800:])
            per_schema.append(sres)
            print(f"[{idx + 1}/{len(selected)}] {entry['dataset']}/"
                  f"{entry['name']} PARITY-ERROR: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            continue
        sres.update(status="compared", parity=parity)
        per_schema.append(sres)
        # reproducer-файлы для REAL-BUG*
        for eng_name, divs in parity["divergences"].items():
            for d in divs:
                if d["class"].startswith("REAL-BUG"):
                    repro = {
                        "class": d["class"], "engine_compared": eng_name,
                        "schema_source": {"source": entry["source"],
                                          "dataset": entry["dataset"],
                                          "name": entry["name"]},
                        "schema": schema_obj,
                        "prefix_token_ids": d["prefix_token_ids"],
                        "prefix_bytes": repr(b"".join(
                            env.token_text(t)
                            for t in d["prefix_token_ids"])),
                        "token_id": d.get("token_id"),
                        "token_bytes": d.get("token_bytes"),
                        "direction": d.get("direction"),
                        "tokenizer": d["tokenizer"],
                        "profile": PROFILE,
                        "detail": d["detail"],
                    }
                    with open(os.path.join(
                            args.reproducers_dir,
                            f"repro_{repro_idx:04d}.json"), "w",
                            encoding="utf-8") as f:
                        json.dump(repro, f, ensure_ascii=False, indent=1)
                    repro_idx += 1
        print(f"[{idx + 1}/{len(selected)}] {entry['dataset']}/{entry['name']} "
              f"префиксов={parity['prefixes']} "
              f"({time.time() - t_start:.0f} c)", file=sys.stderr, flush=True)

    # --- агрегаты ---
    aggregate = {}
    for eng_name in ("xgrammar", "llguidance"):
        schemas_compared = 0
        prefixes = 0
        raw_exact = 0
        classes = {}
        real_bugs = []
        competitor_anomalies = []
        compile_errors = 0
        term_mismatch = 0
        for sres in per_schema:
            eng_info = sres.get(eng_name)
            if eng_info is not None and eng_info.get("status") == "compile-error":
                compile_errors += 1
            if sres.get("status") != "compared":
                continue
            par = sres["parity"]
            if eng_name not in par["divergence_classes"]:
                continue
            schemas_compared += 1
            prefixes += par["prefixes"]
            raw_exact += par["raw_exact"].get(eng_name, 0)
            term_mismatch += par["termination_mismatch"].get(eng_name, 0)
            bugs = {}
            anomalies = {}
            for cls, n in par["divergence_classes"][eng_name].items():
                classes[cls] = classes.get(cls, 0) + n
                if cls.startswith("REAL-BUG"):
                    bugs[cls] = n
                elif not cls.startswith(("expected:", "unclassified",
                                         "inconclusive")):
                    anomalies[cls] = n
            if bugs:
                real_bugs.append({"dataset": sres["dataset"],
                                  "name": sres["name"], "classes": bugs})
            if anomalies:
                competitor_anomalies.append(
                    {"dataset": sres["dataset"], "name": sres["name"],
                     "classes": anomalies})
        aggregate[eng_name] = {
            "schemas_compared": schemas_compared,
            "compile_errors_on_our_subset": compile_errors,
            "prefixes": prefixes,
            "raw_exact_prefixes": raw_exact,
            "raw_match_rate": (raw_exact / prefixes) if prefixes else None,
            "termination_mismatches": term_mismatch,
            "divergence_classes": dict(sorted(classes.items(),
                                              key=lambda kv: -kv[1])),
            "schemas_with_real_bug_candidates": real_bugs,
            "schemas_with_competitor_anomalies": competitor_anomalies,
        }

    status_counts = {}
    for sres in per_schema:
        st = sres.get("status", "?")
        status_counts[st] = status_counts.get(st, 0) + 1
    report = {
        "meta": {
            "tokenizer": {"name": TOKENIZER_NAME,
                          "revision": TOKENIZER_REVISION,
                          "vocab_size": env.vocab_size},
            "profile": PROFILE,
            "seed": args.seed,
            "sampling_rule": sampling_notes,
            "traces_per_schema": args.traces,
            "max_steps": args.max_steps,
            "classify_budget_per_schema": args.classify_budget,
            "engine_versions": {
                "zig_constraints": env.zc.__version__,
                "xgrammar": _version("xgrammar"),
                "llguidance": _version("llguidance"),
            },
            "reproducers_written": repro_idx,
            "status_counts": status_counts,
            "wall_seconds": round(time.time() - t_start, 3),
            "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        },
        "aggregate": aggregate,
        "per_schema": per_schema,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    bc.emit({"status": "OK", "schemas": len(selected),
             "compared": sum(1 for s in per_schema
                             if s.get("status") == "compared"),
             "reproducers": repro_idx,
             "wall_seconds": report["meta"]["wall_seconds"]})


def _version(pkg):
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return None


if __name__ == "__main__":
    main()
