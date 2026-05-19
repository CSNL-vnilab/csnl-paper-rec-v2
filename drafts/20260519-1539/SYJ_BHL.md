# SYJ+BHL — 추천 검토

채택 #1: **Process dynamics of serial biases in visual perception and working memory processes**  
composite **8** (best_dim D3) · Psychonomic Bulletin & Review · 2025-05-27 · tier=strict · fulltext=pdf  
DOI: 10.3758/s13423-025-02714-5

grounding: bhl_paradigm_pilot.manipulation_variables.fitted_parameters = DoG (Difference of Gaussians) via scipy.optimize.curve_fit, fit_axis_x = reference(2nd item) orientation - target(1st item) orientation, fit_target_y = mean centered estimation error; and purpose.research_question = whether the type/processing of the reference (active discrimination vs passive) affects the estimation distractor effect (bias). This paper fits the identical first-derivative-of-Gaussian model to centered (circular-mean) error vs the relative feature difference of the prior item, with the amplitude alpha as the bias-direction/magnitude parameter, and directly contrasts an immediate-report (perceptual) vs delayed-WM processing locus on bias direction (repulsion->attraction) — inferred mapping onto BHL's question of how reference processing shapes the estimation bias; modality (behavior only) also matches modalities.behavior=true.  
verbatim quote: "We modeled circular mean errors as a function of the relative color differences with the first derivative of Gaussian (DoG) function, given by where y is the circular mean error, x is the relative color of the previous trial, and α is the amplitude of the peak of the curve. Of the primary interest, the amplitude α determines the direction and magnitude of serial bias"

review: verdict=pass iterations=3 banned_hits=[] 추천근거자수=274

## 채택 draft (검토용 — 미발송)

channels: ['C0B3FTNR00J', 'C0B39GVLKCK'] · DM: ['SYJ', 'BHL']

### channel_text
```
조수영 · 이보현 연구원께,

논문: Process dynamics of serial biases in visual perception and working memory processes
저자: Park HB — Psychonomic Bulletin & Review, 2025-05
DOI: https://doi.org/10.3758/s13423-025-02714-5

추천 근거: bhl_paradigm_pilot 의 DoG 적합(scipy.optimize.curve_fit; x = reference−target orientation, y = centered estimation error)이 reference 처리와 estimation distractor effect 관계라는 research_question 에 mapping 됩니다. 같은 DoG 로 "α determines the direction and magnitude of serial bias" 라 합니다.

활용: 본 논문의 즉시 보고 대 지연 작업기억 처리 위치 대비를 위 DoG 적합의 centered estimation error 대 reference−target orientation 결과 해석 기준으로 검토하실 수 있습니다.

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```

### dm_ping_text
```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
{permalink}
```

## 후보 7건 (operator 교체 선택용)

- **#1** comp 8 (D3) [strict/pdf] — Process dynamics of serial biases in visual perception and working memory processes  
  Psychonomic Bulletin & Review · 2025-05-27 · DOI 10.3758/s13423-025-02714-5  
  grounding: bhl_paradigm_pilot.manipulation_variables.fitted_parameters = DoG (Difference of Gaussians) via scipy.optimize.curve_fit, fit_axis_x = reference(2nd item) orientation - target(1st item) orientation, f
- **#2** comp 8 (D3) [strict/pdf] — A direct neural signature of serial dependence in working memory  
  eLife · 2025-06-23 · DOI 10.7554/elife.99478  
  grounding: bhl_paradigm_pilot.manipulation_variables.fitted_parameters = DoG via scipy.optimize.curve_fit (DoG x = reference(2nd item)-target(1st item) orientation, y = mean centered estimation error); and code_
- **#3** comp 8 (D2) [strict/pdf] — Attractive serial dependence arises during decision-making  
  PLOS Biology · 2025-08-22 · DOI 10.1371/journal.pbio.3003333  
  grounding: bhl_paradigm_pilot.purpose.research_question = whether the type of discrimination on the reference affects the estimation distractor effect (bias), with manipulation_variables.fitted_parameters.item_o
- **#4** comp 7 (D2) [strict/pdf] — Visual working memory prioritization modulates serial dependence beyond simple attentional effects  
  BMC Biology · 2025-11-14 · DOI 10.1186/s12915-025-02441-2  
  grounding: bhl_paradigm_pilot.manipulation_variables.independent_vars[0] = discrimination_reference_relation (categorical: active_target_compared vs passive_to_external_axis) and independent_vars[1] = feature_cu
- **#5** comp 6 (D1) [strict/abstract] — Early Visual Cortex Represents Sensory and Mnemonic Orientations in Separate Subspaces with Preserved Geometry  
  bioRxiv · 2026-04-21 · DOI 10.64898/2026.04.20.718367  
  grounding: bhl_paradigm_pilot.background.prior_studies[0].doi = 10.1016/j.neuron.2025.07.003 (Lim et al. 2025 Neuron — WM Representation in early visual cortex, DET task). This bioRxiv preprint is authored by th
- **#6** comp 6 (D3) [strict/pdf] — Similarity-driven compression during encoding supports biased but more precise working memory  
  Journal of Vision · 2026-04-01 · DOI 10.1167/jov.26.4.8  
  grounding: bhl_paradigm_pilot.manipulation_variables.independent_vars[1] = feature_cued (retro-cue: orientation/size/ignore) and the attractive-vs-repulsive estimation-bias structure analyzed via the DoG. This s
- **#7** comp 5 (D2) [strict/pdf] — Retinotopic Spatial Working Memory Representations Are Not Affected by Task-irrelevant Visual Stimuli  
  Journal of Cognitive Neuroscience · 2026-03-01 · DOI 10.1162/jocn.a.109  
  grounding: bhl_paradigm_pilot.purpose.research_question = whether a reference/distractor stimulus affects the estimation (here: continuous spatial recall) — this paper provides a contrasting null: a brief task-i