"""
Part 14.3: exactly two simple, interpretable model factories -- no
hyperparameter search, no gradient boosting (see
ml_modeling_feasibility_part14.md for why XGBoost, though available in
this environment, was deliberately not used: with only 25 independent
positive groups, a higher-capacity boosted-tree model has more room to
overfit noise than to find real signal, and would cost interpretability
without a credible way to validate the extra complexity was worth it).

Both use `class_weight="balanced"` (a single, well-known, non-tuned
setting appropriate for severe imbalance) and otherwise reasonable fixed
defaults -- never a grid/random search over hyperparameters.
"""
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

RANDOM_STATE = 42


def make_logistic_regression() -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("logreg", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE)),
    ])


def make_random_forest() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=300,
        max_depth=5,
        min_samples_leaf=5,
        class_weight="balanced_subsample",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


def logistic_regression_coefficients(fitted_pipeline: Pipeline, feature_names: list[str]) -> list[tuple[str, float]]:
    """Coefficients are in STANDARDIZED-feature units (after StandardScaler)
    -- comparable to each other in this fitted model, not in the features'
    raw physical units, and not a claim of causal effect size."""
    coefs = fitted_pipeline.named_steps["logreg"].coef_[0]
    return sorted(zip(feature_names, coefs), key=lambda kv: abs(kv[1]), reverse=True)


def random_forest_importances(fitted_model: RandomForestClassifier, feature_names: list[str]) -> list[tuple[str, float]]:
    """Impurity-based feature_importances_ -- biased toward
    high-cardinality/continuous features and reflects correlation, not
    causation. See report for the caveat restated in plain language."""
    return sorted(zip(feature_names, fitted_model.feature_importances_), key=lambda kv: kv[1], reverse=True)
