from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models import models


def _legacy_schema(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                status VARCHAR,
                content_plan_confirmed BOOLEAN DEFAULT 0,
                style_id VARCHAR,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE slides (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                page_num INTEGER NOT NULL,
                type VARCHAR,
                status VARCHAR,
                error_msg TEXT,
                content_json JSON,
                visual_json JSON,
                prompt_text TEXT,
                image_path VARCHAR
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE reference_images (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                role VARCHAR
            )
            """
        )


def test_runtime_schema_repair_makes_legacy_sqlite_project_api_usable(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-pptgod.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    _legacy_schema(engine)

    # create_all creates missing tables but intentionally does not alter existing ones.
    models.Base.metadata.create_all(bind=engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    main_module._ensure_runtime_mvp_schema()

    columns = {
        table: {col["name"] for col in inspect(engine).get_columns(table)}
        for table in ("projects", "slides", "reference_images")
    }
    assert {
        "tester_id",
        "style_proposal",
        "selected_style",
        "selected_template_recommendations",
        "intent_contract",
        "has_unread_notification",
        "unread_notification_message",
    }.issubset(columns["projects"])
    assert "type_locked" in columns["slides"]
    assert {
        "slide_id",
        "process_mode",
        "asset_name",
        "asset_kind",
        "usage_note",
        "asset_analysis",
        "logo_anchor",
    }.issubset(columns["reference_images"])

    Session = sessionmaker(bind=engine)
    db = Session()
    tester = models.TesterUser(
        display_name="线上用户",
        login_key="线上用户",
        passcode_hash="salt:hash",
    )
    db.add(tester)
    db.flush()
    project = models.Project(title="线上烟测", tester_id=tester.id)
    db.add(project)
    db.commit()

    projects = db.query(models.Project).filter(models.Project.tester_id == tester.id).all()

    assert len(projects) == 1
    assert projects[0].has_unread_notification in {False, 0}
