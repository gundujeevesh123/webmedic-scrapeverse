## Benchmark - controlled breakage across 11 fixture layouts, 20 records each

|  System      | Runs | Healthy | Field accuracy | Completeness | Repairs | False repairs | MTTR   |
| ------------ | :--: | :-----: | :------------: | :----------: | :-----: | :-----------: | :----: |
| **Traditional** | 11 | 7 / 11 | 89.77% | 89.77% | - | 0 | - |
| **WebMedic** | 11 | 11 / 11 | 100.00% | 100.00% | 4 / 4 | 0 | 716 ms |

**Detector precision:** 1.00 · **recall:** 1.00 · **F1:** 1.00 · (4 TP · 7 TN · 0 FP · 0 FN)

### Per-fixture detail

| Fixture | Traditional health / status | WebMedic health / status | Repair | Confidence | MTTR |
| ------- | -------------------------- | ------------------------ | :----: | :--------: | :--: |
| `v10_partial` | 66.2% repair_required | 100.0% healthy | yes | 92.5% | 236 ms |
| `v11_semantic` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v1_healthy` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v2_rename_class` | 66.2% repair_required | 100.0% healthy | yes | 92.5% | 412 ms |
| `v3_dataattr` | 47.5% repair_required | 100.0% healthy | yes | 92.5% | 1271 ms |
| `v4_change_nesting` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v5_move_price` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v6_label_change` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v7_decoy` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v8_pagination` | 100.0% healthy | 100.0% healthy | no | - | - |
| `v9_combined` | 66.2% repair_required | 100.0% healthy | yes | 92.5% | 943 ms |
