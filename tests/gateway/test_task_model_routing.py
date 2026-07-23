from unittest.mock import patch

import gateway.run as gateway_run
from gateway.task_model_routing import resolve_task_model_route


CONFIG = {
    "task_model_routing": {
        "enabled": True,
        "platforms": ["feishu"],
        "routes": {
            "quick": {"provider": "deepseek", "model": "deepseek-v4-flash"},
            "content": {"provider": "openai-codex", "model": "gpt-5.6-sol"},
            "decision": {"provider": "moa", "model": "decision"},
            "code_review": {"provider": "moa", "model": "code-review"},
        },
    }
}


def _route(message: str, *, platform: str = "feishu"):
    return resolve_task_model_route(message, CONFIG, platform=platform)


def test_routes_safe_rewrite_to_flash():
    route = _route("把下面这段通知压缩成三条要点：本周门店盘点安排如下……")

    assert route is not None
    assert route.name == "quick"
    assert route.provider == "deepseek"
    assert route.model == "deepseek-v4-flash"


def test_routes_low_risk_content_draft_to_sol():
    route = _route("写一段酷熊单人口播，主题是 AI 不好用先给它岗位，不查资料，只要初稿。")

    assert route is not None
    assert route.name == "content"
    assert route.provider == "openai-codex"
    assert route.model == "gpt-5.6-sol"


def test_routes_explicit_high_stakes_decision_to_moa():
    route = _route("请就合资公司的股权安排做重大决策会诊，给出方案和反例。")

    assert route is not None
    assert route.name == "decision"
    assert route.provider == "moa"
    assert route.model == "decision"


def test_routes_explicit_complex_code_review_to_moa():
    route = _route("请对这次数据库迁移和权限链路做复杂代码审查。")

    assert route is not None
    assert route.name == "code_review"
    assert route.provider == "moa"
    assert route.model == "code-review"


def test_risk_or_tool_request_never_downgrades_to_flash():
    route = _route("查一下当前 OA 待办，再把结果总结成三条。")

    assert route is None


def test_research_backed_content_stays_on_terra():
    route = _route("查一下仁和堂公开资料后，写一篇周年庆讲话稿。")

    assert route is None


def test_explicit_command_is_not_overridden():
    route = _route("/model deepseek-v4-flash")

    assert route is None


def test_other_platform_is_not_routed_when_platform_is_scoped():
    route = _route("把下面这段通知压缩成三条要点：本周门店盘点安排如下……", platform="weixin")

    assert route is None


def test_gateway_applies_selected_route_with_resolved_provider_runtime():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_model_overrides = {}

    with patch.object(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        return_value={"provider": "deepseek", "api_key": "test-key"},
    ):
        model, runtime = runner._apply_task_model_route(
            "把下面这段通知压缩成三条要点：本周门店盘点安排如下……",
            "gpt-5.6-terra",
            {"provider": "openai-codex", "api_key": "main-key"},
            user_config=CONFIG,
            platform="feishu",
            session_key="session-1",
        )

    assert model == "deepseek-v4-flash"
    assert runtime["provider"] == "deepseek"


def test_gateway_preserves_explicit_session_model_override():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_model_overrides = {"session-1": {"model": "grok-4.5"}}

    model, runtime = runner._apply_task_model_route(
        "把下面这段通知压缩成三条要点：本周门店盘点安排如下……",
        "grok-4.5",
        {"provider": "custom:grok-4.5", "api_key": "main-key"},
        user_config=CONFIG,
        platform="feishu",
        session_key="session-1",
    )

    assert model == "grok-4.5"
    assert runtime["provider"] == "custom:grok-4.5"


def test_router_accepts_json_scalar_values_written_by_config_cli():
    cli_config = {
        "task_model_routing": {
            "enabled": True,
            "platforms": '["feishu"]',
            "routes": {
                "quick": '{"provider":"deepseek","model":"deepseek-v4-flash"}',
                "content": '{"provider":"openai-codex","model":"gpt-5.6-sol"}',
            },
        }
    }

    route = resolve_task_model_route("把这段话改写得更简洁：今天开会讨论门店盘点。", cli_config, platform="feishu")

    assert route is not None
    assert route.name == "quick"
    assert route.model == "deepseek-v4-flash"
