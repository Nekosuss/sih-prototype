import { deriveDecision } from "../../utils/risk.js";

// Part 13: the single dominant PROCEED / REROUTE / SUSPEND indicator --
// the one thing a viewer should be able to read in a glance. Synthesizes
// one decision from whichever real backend result is currently available
// (see utils/risk.js::deriveDecision); computes nothing itself.
//
// riskAwareResult: the baseline Part 6 fastest-vs-safe comparison, present
// as soon as a route is calculated in risk-aware mode.
// hazardDecision: a live Part 8/12 RouteDecision (from a demo hazard or a
// field report) -- takes priority once one exists, since it reflects
// CURRENT hazard state rather than the static planning-time comparison.
export default function AlertPanel({ riskAwareResult, hazardDecision }) {
  const decision = deriveDecision({ riskAwareResult, hazardDecision });
  if (!decision) return null;

  return (
    <div className={`decision-banner decision-banner--${decision.level}`}>
      <span className="decision-banner__icon">{decision.icon}</span>
      <div>
        <div className="decision-banner__title">{decision.title}</div>
        <div className="decision-banner__detail">{decision.detail}</div>
      </div>
    </div>
  );
}
