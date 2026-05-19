# JOP — 추천 검토

채택 #1: **Endogenous precision of the number sense**  
composite **8** (best_dim D1) · eLife · 2026-04-15 · tier=strict · fulltext=html  
DOI: 10.7554/elife.101277

grounding: granrdt.purpose.research_question='channel capacity 제약 하에서 어떤 cost function 형태 (Power / ExpSat / Weibull) 이 가장 잘 설명하는가' + granrdt.manipulation_variables.fitted_parameters=[Power(p,lambda), ExpSat(sigma,lambda), Weibull(alpha,beta,lambda)] + granrdt.hypothesis='Mutual information vs distortion trade-off (Blahut-Arimoto) 가 granularity-induced quantization 의 정량적 model'; time2dist prior-width/skew manipulation. This paper fits competing efficient-coding resource-cost forms (linear vs logarithmic encoding μ(x), Fisher information I(x)=(μ′(x)/(ν·w^α))²) under a resource constraint while manipulating prior width — the same cost-function-form model-selection problem granrdt poses, and the prior-width design parallels time2dist's same-range/different-distribution paradigm. Inferred mapping, not researcher-confirmed.  
verbatim quote: "The double dependence of the imprecision — both on the prior and on the task — is consistent with the optimization of a tradeoff between the expected reward, different for each task, and a resource cost of the encoding neurons’ activity. Comparing the two tasks allows us to clarify the form of the resource constraint."

review: verdict=pass iterations=1 banned_hits=[] 추천근거자수=260

## 채택 draft (검토용 — 미발송)

channels: ['C0B3FTHAVR8'] · DM: ['JOP']

### channel_text
```
박준오 연구원께,

논문: Endogenous precision of the number sense
저자: Prat-Carrabin A, Woodford M — eLife, 2026-04
DOI: https://doi.org/10.7554/elife.101277

추천 근거: granrdt 의 cost function 형태(Power/ExpSat/Weibull) 선택 문제와, 본 논문이 두 과제의 resource-cost 형태를 prior width 조작으로 비교하는 설정이 mapping 됩니다. 본문은 두 과제 비교가 "clarify the form of the resource constraint" 한다고 기술하며, 이 prior 폭 조작은 time2dist 의 distribution_skew 설계와도 비교 가능한 것으로 보입니다.

활용: granrdt 의 Power/ExpSat/Weibull cost function 모델 선택 절차에 본 논문의 estimation·discrimination 교차 비교 설계를 대조 기준으로 검토하실 수 있습니다.

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```

### dm_ping_text
```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
{permalink}
```

## 후보 10건 (operator 교체 선택용)

- **#1** comp 8 (D1) [strict/html] — Endogenous precision of the number sense  
  eLife · 2026-04-15 · DOI 10.7554/elife.101277  
  grounding: granrdt.purpose.research_question='channel capacity 제약 하에서 어떤 cost function 형태 (Power / ExpSat / Weibull) 이 가장 잘 설명하는가' + granrdt.manipulation_variables.fitted_parameters=[Power(p,lambda), ExpSat(sigm
- **#2** comp 8 (D2) [strict/html] — Attractive serial dependence arises during decision-making  
  PLoS Biology · 2025-08-22 · DOI 10.1371/journal.pbio.3003333  
  grounding: ringrepsca.purpose.hypothesis='History effect 가 decision commitment 와 무관하게 point-estimate 단에서 전이된다' + ringrepsca.purpose.scientific_aim='Serial dependence / history effect 의 estimation-only 구현으로 senso
- **#3** comp 7 (D1) [strict/html] — The influence of temporal context on vision over multiple time scales  
  eLife · 2025-10-01 · DOI 10.7554/elife.106614  
  grounding: ringrepsca.purpose.research_question='Decision commitment 없이도 point estimate 의 history effect (sensory adaptation 와 별개) 가 transfer 되는가' + ringrepsca.manipulation_variables.regression_model='Est_t ~ pr
- **#4** comp 6 (D2) [strict/abstract] — Serial dependence in time perception requires consistent motor responses  
  British Journal of Psychology · 2026-04-03 · DOI 10.1111/bjop.70070  
  grounding: ringrepsca.purpose.research_question='Decision commitment 없이도 point estimate 의 history effect (sensory adaptation 와 별개) 가 transfer 되는가; absolute estimation 과 relative estimation 사이의 transfer 패턴' + tim
- **#5** comp 6 (D3) [strict/html] — Stronger reliance on visual perceptual history in individuals with higher math anxiety  
  BMC Biology · 2025-10-14 · DOI 10.1186/s12915-025-02417-2  
  grounding: ringrepsca.dependent_vars=[current_estimate, prev_stim_regression_coef, prev_est_regression_coef] + ringrepsca.fitted_parameters=[leak_abs, leak_rel] (central tendency = prior leak). This paper measur
- **#6** comp 6 (D3) [strict/html] — Human brain integrates both unconditional and conditional timing statistics to guide expectation  
  PLoS Biology · 2025-10-23 · DOI 10.1371/journal.pbio.3003459  
  grounding: time2dist.purpose.research_question='동일 range 내 skewness가 다른 두 자극 분포를 학습할 때 Bayesian observer parameter가 어떻게 재구성되는가' + time2dist.independent_vars=[duration, distribution_skew]. This paper models how t
- **#7** comp 6 (D2) [strict/html] — Motion direction biases around the clock: Learned and in-built direction priors pull perception and pursuit apart  
  Journal of Vision · 2026-03-01 · DOI 10.1167/jov.26.3.11  
  grounding: time2dist.hypothesis='Fixed subjective prior recovery + encoding model selection ... Absolute posterior 분포 전체가 Relative space 로 transfer 된다' + ringrepsca Bayesian observer (leak/prior). This paper mea
- **#8** comp 5 (D2) [strict/html] — The effect of sequence stability on serial dependence  
  Journal of Vision · 2026-03-01 · DOI 10.1167/jov.26.3.6  
  grounding: ringrepsca.purpose.scientific_aim='Serial dependence / history effect 의 estimation-only 구현으로 sensory adaptation 과 decision-driven attractor 를 분리'. This paper shows serial dependence is stronger in uns
- **#9** comp 5 (D5) [strict/html] — Neuronal populations across the cortex underlie discrete, categorical, and subjective representations of visual durations  
  PLoS Biology · 2026-03-26 · DOI 10.1371/journal.pbio.3003704  
  grounding: grannmds.purpose='Psychological space 의 geometric reconstruction 으로 granularity effect 의 구조적 origin 식별' (discrete vs categorical representation) + time2dist (duration estimation). The paper distinguis
- **#10** comp 3 (D5) [strict/html] — The perceptual average in ensemble representation: Neither perceptual nor an average  
  Attention, Perception, & Psychophysics · 2025-11-26 · DOI 10.3758/s13414-025-03187-3  
  grounding: Weak/tangential: relates to magnitude summary statistics relevant to time2dist/ringrepsca estimation, but the paper argues ensemble averages are memory-control constructs, not perceptual, with no esti