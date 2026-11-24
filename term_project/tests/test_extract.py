import io
import zipfile

import pandas as pd
import pytest
import requests

from extract import ExtractError, download_dataset, read_tables


class FakeResponse:
    def __init__(self, *, payload=None, content=None):
        self.payload = payload
        self.content = content
        self.response = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.content


def make_dataset_archive():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("lego_sets_1975_2025.csv", "set_num,name\n001-1,Set\n")
        archive.writestr("lego_themes_1975_2025.csv", "id,theme_name,parent_theme\n1,City,City\n")
        archive.writestr(
            "lego_minifigures_1975_2025.csv",
            "fig_num,name\nfig-1,Figure\n",
        )
    return content.getvalue()


def test_download_dataset_reports_request_failure(monkeypatch, tmp_path):
    def fail_request(*_args, **_kwargs):
        raise requests.ConnectionError("network unavailable")

    monkeypatch.setattr(requests.Session, "get", fail_request)

    with pytest.raises(ExtractError, match="Kaggle metadata request failed"):
        download_dataset("demo/dataset", cache_dir=tmp_path)


def test_download_dataset_rejects_invalid_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(
        requests.Session,
        "get",
        lambda *_args, **_kwargs: FakeResponse(payload=[]),
    )

    with pytest.raises(ExtractError, match="invalid dataset metadata"):
        download_dataset("demo/dataset", cache_dir=tmp_path)


def test_download_dataset_caches_by_version_and_reads_tables(monkeypatch, tmp_path):
    archive_content = make_dataset_archive()
    download_calls = []

    def get_metadata(*_args, **_kwargs):
        return FakeResponse(payload={"currentVersionNumber": 7})

    def download_archive(_session, _url, *, json, **_kwargs):
        download_calls.append(json["datasetVersionNumber"])
        return FakeResponse(content=archive_content)

    monkeypatch.setattr(requests.Session, "get", get_metadata)
    monkeypatch.setattr(requests.Session, "post", download_archive)

    archive_path, version = download_dataset("demo/dataset", cache_dir=tmp_path)
    tables = read_tables(archive_path)
    cached_path, cached_version = download_dataset("demo/dataset", cache_dir=tmp_path)

    assert version == cached_version == 7
    assert archive_path == cached_path
    assert download_calls == [7]
    assert tables["sets"].loc[0, "set_num"] == "001-1"
    assert tables["themes"].loc[0, "theme_name"] == "City"
    assert tables["minifigures"].loc[0, "fig_num"] == "fig-1"


def test_read_tables_reports_missing_csv(tmp_path):
    archive_path = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("lego_themes_1975_2025.csv", "id,theme_name,parent_theme\n")

    with pytest.raises(ExtractError, match="lego_sets_1975_2025.csv is missing"):
        read_tables(archive_path)
