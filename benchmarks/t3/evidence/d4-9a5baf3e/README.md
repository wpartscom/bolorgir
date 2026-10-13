# D4 round evidence index (candidate 9a5baf3e)

Machine-readable tables and summaries live in benchmarks/t3/evidence/; this directory

carries the tools and the raw replays of the 2026-10-13 round (R11-A2, R11-C2/B2).

| file | sha256 |
|---|---|
| raw-r11a2.tar.gz | fea8fc8163e822a5ad50889cc8a1a9cf8100cf4a7d741f6d3e92c01f404e2121 |
| raw-r11bc2.tar.gz | baa152dea96995707eb622c4c8dadfe84c56f6cd926a9956f90042803cb8510f |
| tools/README_PLAN.md | 81618561f6c492671e4bbde5172889ce038b1f3f642011fd1dd1b475965b2ce4 |
| tools/build_native.sh | f188e9eba0ae696292278a40c61cdb177475e1cab72ec1c903099c20266179fc |
| tools/common.sh | 84558ab2f4c166a9408dab56a5b56e6fa5441ad26defef3a7172bf37fcc4a340 |
| tools/export_specs.py | e96e1e8fbe348d995212e8a4e882469ed8aa2ae302b0c844a8618950f8fcaece |
| tools/git-state-start.txt | b55aaa01bb4c78655f52e9e2b39fc7bd987ffdee534121070c00e88b7b417221 |
| tools/items-r11a2.json | 73d0d6b76cfc9d68541010b9f041727ebcb793ccccae7d84c0f0976ee0f2fe1f |
| tools/make_d4_update.py | 245d3a46f7b5d7022fcc5983d601ad9ac15875a34ea55ac066f74ecb5acce168 |
| tools/make_items.py | 38637e39c726b02fd40005fff223c98ffdd3c4bdd01978192c69538617368cbd |
| tools/make_transitions_table.py | 612d0f0adb806c9c35aadca867be8e6f2452095e100260aa99493899f243e983 |
| tools/native-build.json | 1502a46826444cc093bc44df741498ee8007a13bfb665c41518b6d94bfbf356d |
| tools/native-build.log | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| tools/r11a2.sh | 21973a209a20e81fb04097a64d1956eba7d5f5a316fa1b3e1a8e581f0c276f8b |
| tools/r11a2_specs.json | f989c31830ed7b6d13e91e4313f0cdf1377fc83d1c2a0b4088fac145a8ed20de |
| tools/r11a2_summary.py | ac6ffb847611427402d02599aac3851e6d0737a53c51a11173458eaa5b1453cd |
| tools/r11a2_summary_change_note.md | ae93f3a1de9a34720b2988d4b9470400655e06afaaa10a76ceaac8bfabd0963f |
| tools/r11bc2.sh | e0d465969fae1ce60c1faf0d862cb04fa9905427d1462e51912b178dfd6437e8 |
| tools/r11bc2_summary.py | c22ad399e1af70d9df9bb90c5c92b7727061b16051ab9fd66b08b9a393984e63 |
| tools/replay_e.py | 6da2e8fe7aedb1955161f7489721a3ebb3ee9fcc7a6e21be0ae3be52ba63b318 |
| tools/replay_pass1.py | 56bfbec455aafd43ec4071a326edc80ba8b90b4730aeeac6d7696b44f34c1e61 |

Raw archives: r11a2 (25 results x 6 libraries x 3 rounds, jsonl + logs + manifest),
r11bc2 (C replays on 9a5baf3e and 50cbd80e; B replays of MaskBench bigquery-table).
Access: tar xzf <archive>; the JSONL heads record label, round, library sha256, affinity,
loadavg_start; the logs are the process consoles. The summaries and the package document
are tracked beside this directory (benchmarks/t3/evidence/); the preliminary R6 bundle
(command.txt, console.txt, acceptance, triage, tools, SHA256SUMS) is in
benchmarks/t3/evidence/d4-preliminary-r6/.
