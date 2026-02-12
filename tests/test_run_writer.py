"""Tests for standardized run writer."""

from pathlib import Path

from mks.application.run_writer import create_run, finalize_run, list_output_files


def test_run_writer_creates_manifest_and_summary(tmp_path: Path) -> None:
    ctx = create_run(
        "unit-test-capability",
        inputs={"key": "value"},
        reports_root=str(tmp_path),
    )
    data_file = ctx.output_dir / "data.csv"
    data_file.write_text("a,b\n1,2\n", encoding="utf-8")

    result = finalize_run(
        ctx,
        status="success",
        output_files=list_output_files(ctx.output_dir),
        summary_lines=["# Unit", "- ok"],
    )

    assert result.output_dir.exists()
    assert result.manifest_path.exists()
    assert result.summary_path.exists()
    assert data_file in result.output_files
    assert "unit-test-capability" in result.manifest_path.read_text(encoding="utf-8")
