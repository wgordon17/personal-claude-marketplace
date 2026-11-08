"""Tests for jira_activity.py."""

from datetime import date
from unittest.mock import MagicMock, patch

from standup_tool.collectors.jira_activity import _run_jql, collect


def make_run(stdout: str, returncode: int = 0):
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = returncode
        mock.stdout = stdout
        return mock

    return fake_run


def test_run_jql_returns_empty_on_error():
    with patch("subprocess.run", side_effect=make_run("", returncode=1)):
        result = _run_jql("project = ACME")
    assert result == []


def test_run_jql_parses_tab_separated_fields():
    stdout = "ACME-123\tAdd feature\tIn Progress\tStory\n"
    with patch("subprocess.run", side_effect=make_run(stdout)):
        result = _run_jql("project = ACME")
    assert len(result) == 1
    assert result[0]["key"] == "ACME-123"
    assert result[0]["summary"] == "Add feature"
    assert result[0]["status"] == "In Progress"
    assert result[0]["type"] == "Story"


def test_run_jql_squeezes_consecutive_tabs():
    stdout = "ACME-456\t\tFix bug\t\tDone\t\tBug\n"
    with patch("subprocess.run", side_effect=make_run(stdout)):
        result = _run_jql("project = ACME")
    assert len(result) == 1
    assert result[0]["key"] == "ACME-456"
    assert result[0]["summary"] == "Fix bug"


def test_run_jql_skips_invalid_key_format():
    stdout = "not-a-key\tSome issue\tOpen\tTask\n"
    with patch("subprocess.run", side_effect=make_run(stdout)):
        result = _run_jql("project = ACME")
    assert result == []


def test_run_jql_skips_lines_with_too_few_fields():
    stdout = "ACME-123\tonly two fields\n"
    with patch("subprocess.run", side_effect=make_run(stdout)):
        result = _run_jql("project = ACME")
    assert result == []


def test_run_jql_skips_empty_lines():
    stdout = "\nACME-789\tValid\tOpen\tTask\n\n"
    with patch("subprocess.run", side_effect=make_run(stdout)):
        result = _run_jql("project = ACME")
    assert len(result) == 1


def test_collect_deduplicates_in_sprint_against_assigned():
    assigned_output = "ACME-100\tAssigned issue\tIn Progress\tStory\n"
    sprint_output = (
        "ACME-100\tAssigned issue\tIn Progress\tStory\nACME-200\tSprint only\tTo Do\tTask\n"
    )

    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        jql = cmd[cmd.index("--jql") + 1]
        if "in openSprints" in jql:
            mock.stdout = sprint_output
        elif "updatedDate" in jql or "reporter" in jql or "assignee is EMPTY" in jql:
            mock.stdout = ""
        else:
            mock.stdout = assigned_output
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        result = collect(since_date=date(2026, 4, 20), project="ACME")

    assigned_keys = {i["key"] for i in result["assigned"]}
    sprint_keys = {i["key"] for i in result["in_sprint"]}

    assert "ACME-100" in assigned_keys
    assert "ACME-100" not in sprint_keys
    assert "ACME-200" in sprint_keys


def test_collect_returns_all_five_keys():
    with patch("subprocess.run", side_effect=make_run("")):
        result = collect(project="ACME")
    assert set(result.keys()) == {
        "assigned",
        "recently_updated",
        "in_sprint",
        "created_by_me",
        "unassigned_pool",
    }


def test_collect_uses_project_parameter():
    captured_jql = []

    def fake_run(cmd, **kwargs):
        jql = cmd[cmd.index("--jql") + 1]
        captured_jql.append(jql)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        collect(since_date=date(2026, 4, 18), project="ACME")

    assert len(captured_jql) == 5
    for jql in captured_jql:
        assert "project = ACME" in jql


def test_collect_since_date_used_in_recently_updated_jql():
    captured_jql = []

    def fake_run(cmd, **kwargs):
        jql = cmd[cmd.index("--jql") + 1]
        captured_jql.append(jql)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        collect(since_date=date(2026, 4, 18), project="ACME")

    recently_updated_jql = next(jql for jql in captured_jql if "updatedDate" in jql)
    assert "2026-04-18" in recently_updated_jql
    assert "-7d" not in recently_updated_jql


def test_collect_defaults_to_7d_when_no_since_date():
    captured_jql = []

    def fake_run(cmd, **kwargs):
        jql = cmd[cmd.index("--jql") + 1]
        captured_jql.append(jql)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        collect(project="ACME")

    recently_updated_jql = next(jql for jql in captured_jql if "updatedDate" in jql)
    assert "-7d" in recently_updated_jql


def test_collect_personal_queries_omit_component_filter():
    """Personal queries must not filter by component — catches ungroomed tickets."""
    captured_jql = []

    def fake_run(cmd, **kwargs):
        jql = cmd[cmd.index("--jql") + 1]
        captured_jql.append(jql)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        collect(since_date=date(2026, 4, 20), project="ACME")

    assigned = next(
        j
        for j in captured_jql
        if "assignee = currentUser()" in j and "updatedDate" not in j and "openSprints" not in j
    )
    updated = next(j for j in captured_jql if "updatedDate" in j)
    created = next(j for j in captured_jql if "reporter" in j)
    assert "component" not in assigned
    assert "component" not in updated
    assert "component" not in created

    sprint = next(j for j in captured_jql if "openSprints" in j and "assignee = currentUser()" in j)
    unassigned = next(j for j in captured_jql if "assignee is EMPTY" in j)
    assert "component" not in sprint
    assert "component" not in unassigned


def test_collect_unassigned_pool_scoped_to_own_reports():
    """unassigned_pool must never surface other people's unassigned tickets."""
    captured_jql = []

    def fake_run(cmd, **kwargs):
        jql = cmd[cmd.index("--jql") + 1]
        captured_jql.append(jql)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        collect(project="ACME")

    unassigned = next(j for j in captured_jql if "assignee is EMPTY" in j)
    assert "reporter = currentUser()" in unassigned
