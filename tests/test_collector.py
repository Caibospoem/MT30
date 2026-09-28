"""采集模块测试：基于真实临时 Git 仓库。"""
import subprocess

from app.collector.git_collector import list_commits, load_commit_detail
from scripts.make_demo_repo import main as make_repo


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
                        "PATH": "/usr/bin:/bin:/usr/local/bin"})


def test_list_commits(demo_repo):
    commits = list_commits(demo_repo, branch="main")
    assert len(commits) == 5
    assert len({c["hash"] for c in commits}) == 5
    assert commits[0]["message"] == "数据库变更"  # git log 默认新→旧
    assert commits[-1]["message"] == "修改接口"
    assert all(c["parents"] for c in commits[:-1])  # 根提交无父提交
    # 每条记录应挂上自己的文件列表（分隔符错位会表现为 0 文件）
    assert all(len(c["file_marks"]) >= 1 for c in commits)


def test_list_commits_limit(demo_repo):
    assert len(list_commits(demo_repo, limit=2)) == 2
    assert len(list_commits(demo_repo, limit=100)) == 5


def test_merge_commit_detection(demo_repo):
    _git(demo_repo, "checkout", "-q", "-b", "feature/x")
    open(f"{demo_repo}/f.txt", "w").write("feature work\n")
    _git(demo_repo, "add", "-A")
    _git(demo_repo, "commit", "-q", "-m", "feature commit")
    _git(demo_repo, "checkout", "-q", "main")
    _git(demo_repo, "merge", "-q", "--no-ff", "feature/x", "-m", "merge feature")

    commits = list_commits(demo_repo)
    merges = [c for c in commits if len(c["parents"]) > 1]
    assert len(merges) == 1
    detail = load_commit_detail(demo_repo, merges[0])
    assert detail.is_merge


def test_load_commit_detail(demo_repo):
    commits = list_commits(demo_repo)
    detail = load_commit_detail(demo_repo, commits[-1])  # 最早的提交=修改接口
    assert detail.message == "修改接口"
    assert detail.files, "应有文件变更"
    assert all(f.diff.startswith("@@") or f.diff for f in detail.files)
    assert any(f.path.endswith("OrderExportController.java") for f in detail.files)
