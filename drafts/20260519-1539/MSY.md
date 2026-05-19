# MSY — 추천 검토

채택 #1: **History bias and its perturbation of the stimulus representation in the macaque prefrontal cortex**  
composite **7** (best_dim D1) · The Journal of Physiology · 2026-03-05 · tier=strict · fulltext=pdf  
DOI: 10.1113/jp288070

grounding: cat_mag_main.manipulation_variables.independent_vars.stim ('magnitude' arm) + stim_type (General/Male/Female, feature manipulation); cat_mag_main.background.prior_studies = 'Bernardi & Salzman 2020 — PFC abstraction geometry' (task-dependent representation, candidate prior). The paper tests history bias in a sequential magnitude (distance) discrimination task and shows the attractive pull is modulated by whether stimulus visual features match/mismatch — the same magnitude-task + feature(stim_type)-mismatch axis MSY manipulates — and localizes it to PFC stimulus representation, the lineage of the Bernardi & Salzman prior. Inferred-fit: the magnitude-judgment + feature-mismatch correspondence maps onto, but is not asserted to confirm, MSY's task-dependent generative-model hypothesis.  
verbatim quote: "We showed that the previous stimulus magnitude produced an attractive effect on the current stimulus magnitude and that this effect was stronger when their stimulus features differed. In this case at the neural level we also observed that decoding of the stimulus magnitude achieved the highest accuracy when it matched the magnitude of the preceding stimulus for which the decoder was trained. This indicates that past stimuli can affect magnitude processing already during the stimulus presentation, even before the decision-making process."

review: verdict=pass iterations=2 banned_hits=[] 추천근거자수=268

## 채택 draft (검토용 — 미발송)

channels: ['C0B4A6WAGNL'] · DM: ['MSY']

### channel_text
```
여민수 연구원께,

논문: History bias and its perturbation of the stimulus representation in the macaque prefrontal cortex
저자: Benozzo D, Ferrucci L, Ceccarelli F, Genovesio A — The Journal of Physiology, 2026-03
DOI: https://doi.org/10.1113/jp288070

추천 근거: cat_mag_main 의 magnitude task arm(독립변수 stim)·stim_type 자질 조작 축이 본 논문 설계와 mapping 됩니다. 본문은 "previous stimulus magnitude produced an attractive effect" 가 자질 불일치 시 강해진다고 보고하며 PFC 자극 표상에 위치시킵니다. background.prior_studies 의 Bernardi & Salzman 2020 계열로 이어지고, 가설 대응은 추론된 것입니다.

활용: cat_mag_main 의 scaled_gauss(mu, sigma) history-effect 적합에서 자질 일치/불일치별 끌림 크기를 비교하는 분석 축으로 검토하실 수 있습니다.

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```

### dm_ping_text
```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
{permalink}
```

## 후보 10건 (operator 교체 선택용)

- **#1** comp 7 (D1) [strict/pdf] — History bias and its perturbation of the stimulus representation in the macaque prefrontal cortex  
  The Journal of Physiology · 2026-03-05 · DOI 10.1113/jp288070  
  grounding: cat_mag_main.manipulation_variables.independent_vars.stim ('magnitude' arm) + stim_type (General/Male/Female, feature manipulation); cat_mag_main.background.prior_studies = 'Bernardi & Salzman 2020 — 
- **#2** comp 7 (D2) [strict/pdf] — Between repulsion and attraction in serial biases: Replication of Chen and Bae (2024)  
  Journal of Vision · 2025-07-11 · DOI 10.1167/jov.25.8.13  
  grounding: cat_mag_main.purpose.hypothesis = 'categorical vs. magnitude task structure 가 서로 다른 generative model 을 야기하여 동일 자극에서도 history effect 패턴이 차별화된다' and the project's scaled_gauss(mu,sigma) Bayesian fit of 
- **#3** comp 7 (D5) [strict/pdf] — Neural representations of visual categories are dynamically tailored to the discrimination required by the task  
  Cerebral Cortex · 2025-08-06 · DOI 10.1093/cercor/bhaf212  
  grounding: cat_mag_main.purpose.research_question = '동일 자극(StyleGAN2 gender-degree 얼굴)에 대해 categorization vs. magnitude task 가 서로 다른 generative model 을 recruit' and connected_graph.shared_paradigm_with face_cond
- **#4** comp 6 (D2) [strict/abstract] — Belief updating in decision-variable space: More fine-grained choices attract future ones more strongly  
  iScience · 2025-06-07 · DOI 10.1016/j.isci.2025.112844  
  grounding: cat_mag_main.purpose.hypothesis = 'categorical vs. magnitude task structure 가 서로 다른 generative model 을 야기하여 동일 자극에서도 history effect 패턴이 차별화된다' + fitted_parameters scaled_gauss(mu,sigma) Bayesian histo
- **#5** comp 6 (D1) [strict/pdf] — Reduced weighting of short-term perceptual priors during auditory perceptual decision-making in psychosis-prone individuals  
  BMC Biology · 2025-10-17 · DOI 10.1186/s12915-025-02412-7  
  grounding: face_cond_ver10.research_question = 'cat_mag_main 의 task-dependent generative model 가설을 Prolific 온라인 데이터(대표본)에서 history-effect 형태로 재현/확장' + history_features [resp_prev] + manipulation_variables.depend
- **#6** comp 6 (D1) [strict/pdf] — The effect of sequence stability on serial dependence  
  Journal of Vision · 2026-03-16 · DOI 10.1167/jov.26.3.6  
  grounding: cat_mag_main.manipulation_variables.fitted_parameters scaled_gauss(mu,sigma) over lag/uncertainty slices + face_cond_ver10.history_features (stim_prev). The paper manipulates sequence stability (const
- **#7** comp 6 (D1) [strict/pdf] — Process dynamics of serial biases in visual perception and working memory processes  
  Psychonomic Bulletin & Review · 2025-05-27 · DOI 10.3758/s13423-025-02714-5  
  grounding: cat_mag_main.purpose.research_question (history effect / repulsive vs attractive) + connected_graph.related_projects_same_lab JOP 'time2dist' (serial dependence framing). The paper dissociates a repul
- **#8** comp 5 (D1) [strict/pdf] — Visual working memory prioritization modulates serial dependence beyond simple attentional effects  
  BMC Biology · 2025-11-14 · DOI 10.1186/s12915-025-02441-2  
  grounding: cat_mag_main.purpose.scientific_aim = 'task structure ... serial dependence/history-effect 양상을 결정' — this paper shows task demands requiring active memory maintenance amplify serial dependence whereas
- **#9** comp 5 (D3) [strict/pdf] — Stronger reliance on visual perceptual history in individuals with higher math anxiety  
  BMC Biology · 2025-10-14 · DOI 10.1186/s12915-025-02417-2  
  grounding: cat_mag_main.purpose.research_question (history effect / serial dependence) — the paper jointly measures serial dependence and central tendency on a magnitude (numerosity) task and attributes both to 
- **#10** comp 5 (D3) [strict/pdf] — Different modality-specific mechanisms mediate serial dependence effects in visual and auditory perception  
  BMC Biology · 2026-03-04 · DOI 10.1186/s12915-026-02515-9  
  grounding: cat_mag_main.purpose.scientific_aim ('serial dependence/history-effect 양상을 결정' — centralised vs separate mechanisms). The paper dissociates feature- vs position-tuned serial dependence across modaliti