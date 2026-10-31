import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mingjian-test-matplotlib")

import pytest

from mingjian.services.project import Project


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path, "合成数据审查")
    yield p
    p.close()


@pytest.fixture
def imported(project, tmp_path):
    def make(text="联系电话：13800138000\n邮箱：review@example.org", name="合成样本.txt"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        document_id = project.import_files(project.import_preview([path]))["imported"][0]
        project.parse(document_id)
        return document_id
    return make


def confirm_all(project, document_id):
    project.review_many(document_id, [{"id": f["id"], "revision": f["revision"], "status": "confirmed"}
                                     for f in project.findings(document_id)])
