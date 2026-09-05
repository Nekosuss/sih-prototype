# Mathematical & Risk Models Reference

This document provides the mathematical specifications, heuristic scoring formulations, pathfinding cost equations, and the proposed Machine Learning training architecture.

---

### 1. Heuristic Risk Engine Formulations

The risk engine computes an explainable risk score $R_s \in [0.0, 1.0]$ for each road segment $s$.

$$R_s = \text{clamp}\Big(w_{\text{slope}} \cdot S_s + w_{\text{gsi}} \cdot H_s + w_{\text{weather}} \cdot W_s + w_{\text{incident}} \cdot I_s, \; 0.0, \; 1.0\Big)$$

Where the baseline configuration weights (`app/config.py`) are:
- $w_{\text{slope}} = 0.35$ (Physical terrain gradient)
- $w_{\text{gsi}} = 0.35$ (Historical landslide inventory)
- $w_{\text{weather}} = 0.20$ (Precipitation / atmospheric factor)
- $w_{\text{incident}} = 0.10$ (Active field reports / hazards)

$$\sum w_i = 1.00$$

---

#### Factor 1: Slope Risk ($S_s$)
Computed from NASA SRTM DEM 30m sampling along the road polyline.
- Lower threshold: $\theta_{\min} = 2.0^\circ$ (considered flat plains)
- Upper saturation threshold: $\theta_{\max} = 25.0^\circ$ (extreme mountain grade)

$$S_s = \begin{cases} 
0.0, & \text{if } \theta_s \le 2.0^\circ \\
\frac{\theta_s - 2.0}{25.0 - 2.0}, & \text{if } 2.0^\circ < \theta_s < 25.0^\circ \\
1.0, & \text{if } \theta_s \ge 25.0^\circ 
\end{cases}$$

If DEM data is missing or invalid, $S_s = 0.0$ (transparently documented as unmeasured).

---

#### Factor 2: Historical Landslide Risk ($H_s$)
Derived from Geological Survey of India (GSI) records spatially joined within a 500m buffer. It combines **event frequency** ($C_s$) and **spatial proximity** ($d_s$ in meters):

$$H_{\text{count}} = \begin{cases}
0.0, & \text{if } C_s = 0 \\
\frac{\ln(1 + C_s)}{\ln(1 + 5)}, & \text{if } 1 \le C_s \le 5 \\
1.0, & \text{if } C_s > 5
\end{cases}$$

$$H_{\text{prox}} = \begin{cases}
0.0, & \text{if } C_s = 0 \text{ or } d_s > 500\text{m} \\
1.0 - \frac{d_s}{500.0}, & \text{if } d_s \le 500\text{m}
\end{cases}$$

$$H_s = 0.70 \cdot H_{\text{count}} + 0.30 \cdot H_{\text{prox}}$$

*Note on Hazard Zonation Integration:* When an official APSAC zonation score $Z_s$ is available, it fuses via maximum rather than summation to avoid double-counting correlated evidence:
$$H_s^{\text{fused}} = \max(H_s, \; Z_s)$$

---

#### Factor 3: Weather Factor ($W_s$)
Mapped from IMD 0.25° daily rainfall ($P_s$ in $mm/\text{day}$) via piecewise linear interpolation anchored to official IMD meteorological categories:

| IMD Category | Daily Rainfall Range | Weather Factor Output ($W_s$) |
|---|---|---|
| **No Rain / Trace** | $0.0\text{ mm}$ | $0.00$ |
| **Light Rainfall** | $2.5\text{ mm}$ | $0.10$ |
| **Moderate Rainfall** | $15.6\text{ mm}$ | $0.30$ |
| **Heavy Rainfall** | $64.5\text{ mm}$ | $0.60$ |
| **Very Heavy / Extreme** | $\ge 204.4\text{ mm}$ | $1.00$ |

Between threshold anchors, linear interpolation is applied:
$$W_s = W_a + \frac{P_s - P_a}{P_b - P_a}(W_b - W_a)$$

---

#### Factor 4: Incident Factor ($I_s$)
Driven by active field reports or simulated hazards:
- Minor Incident: $I_s = 0.20$
- Major Incident: $I_s = 0.50$
- Blocking Incident: $I_s = 1.00$ (and initiates physical edge exclusion)

---

### 2. Pathfinding & Route Optimization Models

#### Edge Cost Function
Standard routing algorithms prioritize purely geometric length or free-flow travel time. This platform uses a **multiplicative risk-penalized edge cost**:

$$C(u, v) = T(u, v) \cdot \Big(1.0 + \kappa_{\text{risk}} \cdot R_{(u,v)}\Big)$$

Where:
- $T(u, v) = \frac{\text{distance\_km}}{\text{speed\_kph}} \times 60$ (baseline travel time in minutes)
- $R_{(u,v)} \in [0.0, 1.0]$ is the segment's composite risk score
- $\kappa_{\text{risk}} = 2.0$ is the risk penalty scalar (`RISK_WEIGHT` in `app/config.py`)

*Why Multiplicative?* An additive penalty ($T + \lambda R$) penalizes a 100m segment the exact same flat minutes as a 20km mountain pass. Multiplicative scaling makes the penalty proportional to physical exposure along the hazardous segment.

#### Hard Pruning Constraint
Before running Dijkstra pathfinding, the graph is dynamically filtered:
$$E_{\text{valid}} = \Big\{ (u, v) \in E \;\Big|\; R_{(u,v)} < 0.65 \;\land\; \text{Status}_{(u,v)} \neq \text{"closed"} \Big\}$$

Segments with $R_{(u,v)} \ge 0.65$ (`HARD_UNSAFE_RISK_THRESHOLD`) or marked `closed` are pruned entirely from the search tree.

---

### 3. Route Aggregate Risk & Decision Theory

A whole route $\mathcal{P} = \{s_1, s_2, \dots, s_k\}$ cannot be evaluated by simple mean risk, as a single catastrophic landslide on an otherwise safe highway would be dangerously diluted.

$$\mathcal{R}_{\text{route}} = 0.70 \cdot \max_{s \in \mathcal{P}}(R_s) + 0.30 \cdot \left(\frac{1}{k}\sum_{i=1}^k R_{s_i}\right)$$

#### Operational Decision Logic (CONTINUE / REROUTE / SUSPEND)
When an active route $\mathcal{P}_{\text{current}}$ is disrupted:
1. **SUSPEND:** If no path exists in $E_{\text{valid}}$ between origin and destination. Dispatch is frozen.
2. **REROUTE:** Triggered if:
   - $\mathcal{P}_{\text{current}}$ is severed by an edge closure or hard-unsafe segment ($R_s \ge 0.65$), OR
   - A valid alternative path $\mathcal{P}_{\text{alt}}$ exists such that:
     $$\mathcal{R}(\mathcal{P}_{\text{current}}) - \mathcal{R}(\mathcal{P}_{\text{alt}}) > \Delta_{\text{hysteresis}} \quad (\Delta_{\text{hysteresis}} = 0.05)$$
3. **CONTINUE:** If the existing route remains structurally safe and the alternative does not clear the hysteresis margin.

---

### 4. Machine Learning Disruption Architecture (Future Blueprint)

To graduate from heuristics to true AI, the system requires training a gradient-boosted decision tree (e.g. XGBoost / LightGBM) to output calibrated disruption probabilities:

$$P(\text{Disruption}_{s, t+24h} = 1 \mid \mathbf{x}_{s, t})$$

#### Feature Vector Design ($\mathbf{x}_{s, t}$)
```
┌─────────────────────────┬────────────────────────────────────────────────────────┐
│ Feature Category        │ Variables                                              │
├─────────────────────────┼────────────────────────────────────────────────────────┤
│ Topography (Static)     │ slope_deg, elevation_m, curvature, aspect, TWI        │
│ Historical (Static)     │ historical_landslide_count, nearest_landslide_dist_m   │
│ Soil / Geology (Static) │ lithology_class, soil_depth, soil_texture              │
│ Meteorological (Dynamic)│ rainfall_1d_mm, rainfall_3d_accum, rainfall_7d_accum,  │
│                         │ antecedent_precipitation_index (API)                   │
│ Infrastructure (Static) │ road_type, surface_quality, cut_slope_exposure_m       │
└─────────────────────────┴────────────────────────────────────────────────────────┘
```

#### Training Target Definition
A binary classification target where:
- $Y = 1$ if an official GSI / BRO landslide or road closure was recorded on segment $s$ within $t + 24\text{h}$.
- $Y = 0$ constructed via **conditions-matched negative sampling**: sampling historical dates on the same mountainous segments where 3-day rainfall was in the 80th–95th percentile, but no disruption occurred. (Uniform random sampling would bias the model toward trivial dry-season predictions).
