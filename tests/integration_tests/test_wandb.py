import functools
from typing import Any
from typing import Callable
from typing import List
from typing import Sequence
from typing import Tuple
from typing import Union
from unittest import mock

import pytest

import optuna
from optuna.integration import WeightsAndBiasesCallback


def _objective_func(trial: optuna.trial.Trial) -> float:

    x = trial.suggest_float("x", low=-10, high=10)
    y = trial.suggest_float("y", low=1, high=10, log=True)
    return (x - 2) ** 2 + (y - 25) ** 2


def _multiobjective_func(trial: optuna.trial.Trial) -> Tuple[float, float]:

    x = trial.suggest_float("x", low=-10, high=10)
    y = trial.suggest_float("y", low=1, high=10, log=True)
    first_objective = (x - 2) ** 2 + (y - 25) ** 2
    second_objective = (x - 2) ** 3 + (y - 25) ** 3

    return first_objective, second_objective


def logging_objective_func(trial: optuna.trial.Trial, log_func: Callable) -> float:
    result = _objective_func(trial)
    log_func({"result": result})
    return result


def logging_multiobjective_func(
    trial: optuna.trial.Trial, log_func: Callable
) -> Tuple[float, float]:
    result0, result1 = _multiobjective_func(trial)
    log_func({"result0": result0, "result1": result1})
    return result0, result1


@mock.patch("optuna.integration.wandb.wandb")
def test_run_initialized(wandb: mock.MagicMock) -> None:

    wandb.sdk.wandb_run.Run = mock.MagicMock

    n_trials = 10
    wandb_kwargs = {
        "project": "optuna",
        "group": "summary",
        "job_type": "logging",
        "mode": "offline",
        "tags": ["test-tag"],
    }

    WeightsAndBiasesCallback(metric_name="mse", wandb_kwargs=wandb_kwargs, as_multirun=False)
    wandb.init.assert_called_once_with(
        project="optuna", group="summary", job_type="logging", mode="offline", tags=["test-tag"]
    )

    wandbc = WeightsAndBiasesCallback(
        metric_name="mse", wandb_kwargs=wandb_kwargs, as_multirun=True
    )
    wandb.run = None

    study = optuna.create_study(direction="minimize")
    _wrapped_func = wandbc.track_in_wandb(lambda t: 1.0)
    wandb.init.reset_mock()
    trial = optuna.create_trial(value=1.0)
    _wrapped_func(trial)

    wandb.init.assert_called_once_with(
        project="optuna", group="summary", job_type="logging", mode="offline", tags=["test-tag"]
    )

    wandb.init.reset_mock()
    study.optimize(_objective_func, n_trials=n_trials, callbacks=[wandbc])

    wandb.init.assert_called_with(
        project="optuna", group="summary", job_type="logging", mode="offline", tags=["test-tag"]
    )

    assert wandb.init.call_count == n_trials

    wandb.init().finish.assert_called()
    assert wandb.init().finish.call_count == n_trials


@mock.patch("optuna.integration.wandb.wandb")
@pytest.mark.parametrize("as_multirun", [True, False])
def test_attributes_set_on_epoch(wandb: mock.MagicMock, as_multirun: bool) -> None:

    # Vanilla update
    wandb.sdk.wandb_run.Run = mock.MagicMock
    expected = {"direction": ["MINIMIZE"]}
    trial_params = {"x": 1.1, "y": 2.2}
    expected_with_params = {"direction": ["MINIMIZE"], "x": 1.1, "y": 2.2}

    study = optuna.create_study(direction="minimize")
    wandbc = WeightsAndBiasesCallback(as_multirun=as_multirun)
    study.enqueue_trial(trial_params)
    study.optimize(_objective_func, n_trials=1, callbacks=[wandbc])

    if as_multirun:
        wandb.run = None
        study.enqueue_trial(trial_params)
        study.optimize(_objective_func, n_trials=1, callbacks=[wandbc])
        wandb.init().config.update.assert_called_once_with(expected_with_params)
    else:
        wandb.run.config.update.assert_called_once_with(expected)


@mock.patch("optuna.integration.wandb.wandb")
@pytest.mark.parametrize("as_multirun", [True, False])
def test_multiobjective_attributes_set_on_epoch(wandb: mock.MagicMock, as_multirun: bool) -> None:

    wandb.sdk.wandb_run.Run = mock.MagicMock
    trial_params = {"x": 1.1, "y": 2.2}
    expected = {"direction": ["MINIMIZE", "MAXIMIZE"]}
    expected_with_params = {"direction": ["MINIMIZE"], "x": 1.1, "y": 2.2}

    study = optuna.create_study(directions=["minimize", "maximize"])
    wandbc = WeightsAndBiasesCallback(as_multirun=as_multirun)

    study.enqueue_trial(trial_params)
    study.optimize(_multiobjective_func, n_trials=1, callbacks=[wandbc])

    if as_multirun:
        wandb.run = None
        study.enqueue_trial(trial_params)
        study.optimize(_multiobjective_func, n_trials=1, callbacks=[wandbc])
        wandb.init().config.update.assert_called_once_with(expected_with_params)
    else:
        wandb.run.config.update.assert_called_once_with(expected)


@mock.patch("optuna.integration.wandb.wandb")
def test_log_api_call_count(wandb: mock.MagicMock) -> None:

    wandb.sdk.wandb_run.Run = mock.MagicMock

    study = optuna.create_study()
    wandbc = WeightsAndBiasesCallback()
    target_n_trials = 10
    study.optimize(_objective_func, n_trials=target_n_trials, callbacks=[wandbc])
    assert wandb.run.log.call_count == target_n_trials

    wandbc = WeightsAndBiasesCallback(as_multirun=True)
    wandb.run.reset_mock()
    _wrapped_logging_func = wandbc.track_in_wandb(
        functools.partial(logging_objective_func, log_func=wandb.run.log)
    )

    study.optimize(_wrapped_logging_func, n_trials=target_n_trials, callbacks=[wandbc])

    assert wandb.run.log.call_count == 2 * target_n_trials

    wandb.run = None
    study.optimize(_objective_func, n_trials=target_n_trials, callbacks=[wandbc])
    assert wandb.init().log.call_count == target_n_trials


@pytest.mark.parametrize(
    "metric,as_multirun,expected",
    [("value", False, ["x", "y", "value"]), ("foo", True, ["x", "y", "foo", "trial_number"])],
)
@mock.patch("optuna.integration.wandb.wandb")
def test_values_registered_on_epoch(
    wandb: mock.MagicMock, metric: str, as_multirun: bool, expected: List[str]
) -> None:
    def assert_call_args(log_func: mock.MagicMock, regular: bool) -> None:
        kall = log_func.call_args
        assert list(kall[0][0].keys()) == expected
        assert kall[1] == {"step": 0 if regular else None}

    wandb.sdk.wandb_run.Run = mock.MagicMock

    if as_multirun:
        wandb.run = None
        log_func = wandb.init().log
    else:
        log_func = wandb.run.log

    study = optuna.create_study()
    wandbc = WeightsAndBiasesCallback(metric_name=metric, as_multirun=as_multirun)
    study.optimize(_objective_func, n_trials=1, callbacks=[wandbc])
    assert_call_args(log_func, bool(wandb.run))


@pytest.mark.parametrize("metric,expected", [("foo", ["x", "y", "foo", "trial_number"])])
@mock.patch("optuna.integration.wandb.wandb")
def test_values_registered_on_epoch_with_logging(
    wandb: mock.MagicMock, metric: str, expected: List[str]
) -> None:

    wandb.sdk.wandb_run.Run = mock.MagicMock

    study = optuna.create_study()
    wandbc = WeightsAndBiasesCallback(metric_name=metric, as_multirun=True)
    _wrapped_func = wandbc.track_in_wandb(
        functools.partial(logging_objective_func, log_func=wandb.run.log)
    )

    study.enqueue_trial({"x": 2, "y": 25})
    study.optimize(_wrapped_func, n_trials=1, callbacks=[wandbc])

    logged_metric = wandb.run.log.mock_calls[0][1][0]

    kall = wandb.run.log.call_args
    assert list(kall[0][0].keys()) == expected
    assert kall[1] == {"step": 0}
    assert logged_metric == {"result": 0}


@pytest.mark.parametrize(
    "metrics,as_multirun,expected",
    [
        ("value", False, ["x", "y", "value_0", "value_1"]),
        ("value", True, ["x", "y", "value_0", "value_1", "trial_number"]),
        (["foo", "bar"], False, ["x", "y", "foo", "bar"]),
        (("foo", "bar"), True, ["x", "y", "foo", "bar", "trial_number"]),
    ],
)
@mock.patch("optuna.integration.wandb.wandb")
def test_multiobjective_values_registered_on_epoch(
    wandb: mock.MagicMock,
    metrics: Union[str, Sequence[str]],
    as_multirun: bool,
    expected: List[str],
) -> None:
    def assert_call_args(log_func: mock.MagicMock, regular: bool) -> None:
        kall = log_func.call_args
        assert list(kall[0][0].keys()) == expected
        assert kall[1] == {"step": 0 if regular else None}

    wandb.sdk.wandb_run.Run = mock.MagicMock

    if as_multirun:
        wandb.run = None
        log_func = wandb.init().log
    else:
        log_func = wandb.run.log

    study = optuna.create_study(directions=["minimize", "maximize"])
    wandbc = WeightsAndBiasesCallback(metric_name=metrics, as_multirun=as_multirun)

    study.optimize(_multiobjective_func, n_trials=1, callbacks=[wandbc])
    assert_call_args(log_func, bool(wandb.run))


@pytest.mark.parametrize(
    "metrics,expected",
    [
        ("value", ["x", "y", "value_0", "value_1", "trial_number"]),
        (("foo", "bar"), ["x", "y", "foo", "bar", "trial_number"]),
    ],
)
@mock.patch("optuna.integration.wandb.wandb")
def test_multiobjective_values_registered_on_epoch_with_logging(
    wandb: mock.MagicMock, metrics: Union[str, Sequence[str]], expected: List[str]
) -> None:

    wandbc = WeightsAndBiasesCallback(as_multirun=True, metric_name=metrics)
    _wrapped_func = wandbc.track_in_wandb(
        functools.partial(logging_multiobjective_func, log_func=wandb.run.log)
    )

    study = optuna.create_study(directions=["minimize", "maximize"])
    study.enqueue_trial({"x": 2, "y": 24})

    study.optimize(_wrapped_func, n_trials=1, callbacks=[wandbc])

    logged_metrics = wandb.run.log.mock_calls[0][1][0]

    kall = wandb.run.log.call_args
    assert list(kall[0][0].keys()) == expected
    assert kall[1] == {"step": 0}
    assert logged_metrics == {"result0": 1, "result1": -1}


@pytest.mark.parametrize("metrics", [["foo"], ["foo", "bar", "baz"]])
@mock.patch("optuna.integration.wandb.wandb")
def test_multiobjective_raises_on_name_mismatch(wandb: mock.MagicMock, metrics: List[str]) -> None:

    wandb.sdk.wandb_run.Run = mock.MagicMock

    study = optuna.create_study(directions=["minimize", "maximize"])
    wandbc = WeightsAndBiasesCallback(metric_name=metrics)

    with pytest.raises(ValueError):
        study.optimize(_multiobjective_func, n_trials=1, callbacks=[wandbc])


@pytest.mark.parametrize("metrics", [{0: "foo", 1: "bar"}])
def test_multiobjective_raises_on_type_mismatch(metrics: Any) -> None:

    with pytest.raises(TypeError):
        WeightsAndBiasesCallback(metric_name=metrics)
