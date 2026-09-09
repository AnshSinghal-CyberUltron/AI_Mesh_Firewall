# Posture Scores

- Corpus_Version: a67337fb06ecc1b6444f2c1eeb6e952a878e0eed
- Reproducible_Command: `cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`
- Target_FPR: 0.01

## Scored set

- Scored_Set: eval
- Malicious_Count: 54
- Benign_Count: 89
- Excluded_Label_Count: 0
- Excluded_Split_Count: 0

## Threshold selection rule

Selected_Threshold maximises recall subject to FPR <= Target_FPR (tolerance 1e-6), tie-broken by lowest FPR then highest threshold; candidate thresholds are the distinct posture scores over the train split; tuned on train split only, then applied to the scored set for measurement.

## Postures

### Posture: Tier1_Only

- Status: **scored**
- Target_FPR not achievable; reporting FPR floor:
  - FPR_Floor: not computable
  - Recall_At_Floor: 0.0000
- Selected_Threshold: 0.0000
- Achieved_FPR: 1.0000
- Paraphrase_Gap: 0.0000

#### Per-family recall (malicious)

| Family | Malicious count | Value |
| --- | --- | --- |
| command_injection | 3 | 1.0000 |
| data_leakage | 6 | 1.0000 |
| goal_hijacking | 5 | 1.0000 |
| jailbreak | 6 | 1.0000 |
| paraphrase | 6 | 1.0000 |
| path_traversal | 2 | 1.0000 |
| prompt_injection | 10 | 1.0000 |
| sql_injection | 5 | 1.0000 |
| tool_overreach | 7 | 1.0000 |
| vector_injection | 4 | 1.0000 |

#### Per-family FPR (benign)

| Family | Benign count | Value |
| --- | --- | --- |
| developer_traffic | 11 | 1.0000 |
| general_benign | 78 | 1.0000 |

### Posture: Tier1_Plus_Policy

- Status: **scored**
- Target_FPR not achievable; reporting FPR floor:
  - FPR_Floor: not computable
  - Recall_At_Floor: 0.0000
- Selected_Threshold: 0.0000
- Achieved_FPR: 1.0000
- Paraphrase_Gap: 0.0000

#### Per-family recall (malicious)

| Family | Malicious count | Value |
| --- | --- | --- |
| command_injection | 3 | 1.0000 |
| data_leakage | 6 | 1.0000 |
| goal_hijacking | 5 | 1.0000 |
| jailbreak | 6 | 1.0000 |
| paraphrase | 6 | 1.0000 |
| path_traversal | 2 | 1.0000 |
| prompt_injection | 10 | 1.0000 |
| sql_injection | 5 | 1.0000 |
| tool_overreach | 7 | 1.0000 |
| vector_injection | 4 | 1.0000 |

#### Per-family FPR (benign)

| Family | Benign count | Value |
| --- | --- | --- |
| developer_traffic | 11 | 1.0000 |
| general_benign | 78 | 1.0000 |

### Posture: Tier1_Plus_Semantic

- Status: **scored**
- Target_FPR not achievable; reporting FPR floor:
  - FPR_Floor: not computable
  - Recall_At_Floor: 0.0000
- Selected_Threshold: 0.0000
- Achieved_FPR: 1.0000
- Paraphrase_Gap: 0.0000

#### Per-family recall (malicious)

| Family | Malicious count | Value |
| --- | --- | --- |
| command_injection | 3 | 1.0000 |
| data_leakage | 6 | 1.0000 |
| goal_hijacking | 5 | 1.0000 |
| jailbreak | 6 | 1.0000 |
| paraphrase | 6 | 1.0000 |
| path_traversal | 2 | 1.0000 |
| prompt_injection | 10 | 1.0000 |
| sql_injection | 5 | 1.0000 |
| tool_overreach | 7 | 1.0000 |
| vector_injection | 4 | 1.0000 |

#### Per-family FPR (benign)

| Family | Benign count | Value |
| --- | --- | --- |
| developer_traffic | 11 | 1.0000 |
| general_benign | 78 | 1.0000 |

