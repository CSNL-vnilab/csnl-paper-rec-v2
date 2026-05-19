# JYK — 추천 검토

채택 #1: **Transitions in dynamical regime and neural mode during perceptual decisions**  
composite **8** (best_dim D3) · Nature · 2025-09-17 · tier=strict · fulltext=pdf  
DOI: 10.1038/s41586-025-09528-4

grounding: dynamic_bias.background.prior_studies[0]=Gu et al. 2025 Neuron (10.1016/j.neuron.2025.07.003, the direct-fork base) + connected_graph.shared_paradigm_with='Gu et al. 2025 Neuron (direct fork)' — JYK's RNN attractor-structure question and dependent_var 'anchor attractor position/depth' / DM 2-way choice. The paper supplies a data-driven flow-field decomposition (autonomous vs input dynamics) that distinguishes competing attractor hypotheses and explicitly evaluates the trained-RNN line-attractor hypothesis underlying JYK's modeling lineage. Correspondence inferred from the shared trained-RNN-attractor paradigm, not asserted.  
verbatim quote: "A third hypothesis, inspired by trained recurrent neural networks, also posits a line attractor (Fig. 1h) but allows for evidence inputs that are not aligned with the line attractor and that accumulate over time through non-normal autonomous dynamics"

review: verdict=pass iterations=2 banned_hits=[] 추천근거자수=270

## 채택 draft (검토용 — 미발송)

channels: ['C0B3FTKE4HY'] · DM: ['JYK']

### channel_text
```
김정예 연구원께,

논문: Transitions in dynamical regime and neural mode during perceptual decisions
저자: Luo T, Kim T, Gupta D, Bondy A, Kopec C, Elliott V, DePasquale B, Brody C — Nature, 2025-09
DOI: https://doi.org/10.1038/s41586-025-09528-4

추천 근거: dynamic_bias 의 task-optimized RNN(Gu et al. 2025 Neuron 직접 fork)에서, 본 논문의 흐름장 분해가 경합 attractor 가설을 구분합니다. 본문은 학습 RNN line-attractor 가설을 "evidence inputs that are not aligned with the line attractor" 로 평가하며, dependent_var 인 anchor attractor 위치·깊이와 mapping 됩니다. 추론된 대응입니다.

활용: 본 논문의 자율·입력 동역학 흐름장 분해 절차를 dynamic_bias 의 anchor attractor 깊이 modulate 분석을 검증하는 대조 기준으로 검토하실 수 있습니다.

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```

### dm_ping_text
```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
{permalink}
```

## 후보 3건 (operator 교체 선택용)

- **#1** comp 8 (D3) [strict/pdf] — Transitions in dynamical regime and neural mode during perceptual decisions  
  Nature · 2025-09-17 · DOI 10.1038/s41586-025-09528-4  
  grounding: dynamic_bias.background.prior_studies[0]=Gu et al. 2025 Neuron (10.1016/j.neuron.2025.07.003, the direct-fork base) + connected_graph.shared_paradigm_with='Gu et al. 2025 Neuron (direct fork)' — JYK's
- **#2** comp 8 (D1) [strict/html] — Nonlinear feedback modulation contributes to the optimization of flexible decision-making  
  eLife · 2025-09-30 · DOI 10.7554/elife.96402  
  grounding: dynamic_bias.manipulation_variables.dependent_vars=['anchor attractor position/depth'] + fitted_parameters w_rnn12 (DM->EM cross-pop) / w_rnn21 (EM->DM cross-pop), and independent_var training_mode (f
- **#3** comp 6 (D3) [strict/abstract] — Single-unit activations confer inductive biases for emergent circuit solutions to cognitive tasks  
  Nature Machine Intelligence · 2025-10-20 · DOI 10.1038/s42256-025-01127-2  
  grounding: dynamic_bias.purpose.research_question (how input noise + loss function change task-optimized RNN representation and attractor structure) + independent_vars circuit_homogeneity (homogeneous/heterogene