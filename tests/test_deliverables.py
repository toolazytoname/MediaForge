from pipeline import deliverables, master_documents, projects, variants


def test_variant_write_projects_two_article_deliverables(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(
        title="项目", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now="2026-08-09T00:00:00+00:00",
        project_id="prj_dlv", projects_root=root,
    )
    master_documents.save_manual(
        "prj_dlv", title="主标题", body="主稿正文" * 80,
        now="2026-08-09T00:01:00+00:00", projects_root=root,
    )
    wechat = variants.create_from_master("prj_dlv", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    toutiao = variants.create_from_master("prj_dlv", "toutiao", now="2026-08-09T00:03:00+00:00", projects_root=root)
    bundle = deliverables.load_deliverables("prj_dlv", projects_root=root)
    by_id = {item.id: item for item in bundle.items}
    assert set(by_id) == {"dlv_article_wechat_mp", "dlv_article_toutiao"}
    assert by_id["dlv_article_wechat_mp"].payload["body"] == wechat.body
    assert by_id["dlv_article_wechat_mp"].version == wechat.version
    assert by_id["dlv_article_toutiao"].title == toutiao.title
    assert (root / "prj_dlv" / "deliverables.json").is_file()
