"""Download the latest Kaggle dataset snapshot and read its three CSV files."""
import os
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

DATASET = os.getenv(
    "KAGGLE_DATASET",
    "abdurrahmanhakanacar/lego-sets-themes-and-minifigures-1975-2025",
)

FILES = {
    "sets": "lego_sets_1975_2025.csv",
    "themes": "lego_themes_1975_2025.csv",
    "minifigures": "lego_minifigures_1975_2025.csv",
}

KAGGLE_METADATA_URL = "https://www.kaggle.com/api/v1/datasets/view"
KAGGLE_DOWNLOAD_URL = (
    "https://api.kaggle.com/v1/datasets.DatasetApiService/DownloadDataset"
)


class ExtractError(RuntimeError):
    """Raised with a readable message when Kaggle data cannot be retrieved."""


def _dataset_parts(handle):
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", handle):
        raise ExtractError("KAGGLE_DATASET must be in owner/dataset format")
    return handle.split("/", 1)


def _request_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "LEGO-Term-Project/1.0"})

    token = os.getenv("KAGGLE_API_TOKEN")
    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    elif username and key:
        session.auth = (username, key)
    elif username or key:
        session.close()
        raise ExtractError("set both KAGGLE_USERNAME and KAGGLE_KEY in .env")

    return session


def download_dataset(handle=DATASET, cache_dir=None):
    """Return the cached archive path and current Kaggle dataset version."""
    owner, slug = _dataset_parts(handle)
    stage = "metadata"
    temporary_path = None
    version = None

    try:
        with _request_session() as session:
            with session.get(
                f"{KAGGLE_METADATA_URL}/{owner}/{slug}",
                timeout=(10, 30),
            ) as response:
                response.raise_for_status()
                metadata = response.json()

            if not isinstance(metadata, dict):
                raise ExtractError("Kaggle returned invalid dataset metadata")

            version = metadata.get("currentVersionNumber")
            if (
                not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
            ):
                raise ExtractError("Kaggle did not return a valid dataset version")

            cache_root = Path(
                cache_dir
                or os.getenv("KAGGLE_CACHE_DIR")
                or os.getenv("KAGGLEHUB_CACHE")
                or Path.home() / ".cache" / "lego-term-project"
            )
            version_dir = cache_root / "datasets" / owner / slug / "versions" / str(version)
            version_dir.mkdir(parents=True, exist_ok=True)
            archive_path = version_dir / "dataset.zip"

            if archive_path.is_file() and zipfile.is_zipfile(archive_path):
                return archive_path, version

            stage = "dataset download"
            with tempfile.NamedTemporaryFile(
                dir=version_dir, suffix=".part", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                with session.post(
                    KAGGLE_DOWNLOAD_URL,
                    json={
                        "ownerSlug": owner,
                        "datasetSlug": slug,
                        "datasetVersionNumber": version,
                    },
                    stream=True,
                    timeout=(10, 180),
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            temporary_file.write(chunk)

            if not zipfile.is_zipfile(temporary_path):
                raise ExtractError("Kaggle returned an invalid dataset archive")

            os.replace(temporary_path, archive_path)
            temporary_path = None
            return archive_path, version
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else "unavailable"
        raise ExtractError(
            f"Kaggle {stage} request failed ({type(exc).__name__}, HTTP {status})"
        ) from exc
    except OSError as exc:
        raise ExtractError(
            f"could not cache Kaggle dataset version {version} ({type(exc).__name__})"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_tables(archive_path):
    tables = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.namelist()
            for name, filename in FILES.items():
                matches = [
                    member for member in members
                    if PurePosixPath(member).name == filename
                ]
                if not matches:
                    raise ExtractError(
                        f"{filename} is missing from the Kaggle dataset archive"
                    )
                if len(matches) > 1:
                    raise ExtractError(
                        f"{filename} appears more than once in the Kaggle dataset archive"
                    )
                with archive.open(matches[0]) as csv_file:
                    tables[name] = pd.read_csv(csv_file)
    except ExtractError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
    ) as exc:
        raise ExtractError(
            f"could not read Kaggle dataset archive ({type(exc).__name__})"
        ) from exc
    return tables


def extract():
    """Return the three tables and the current Kaggle dataset version."""
    archive_path, version = download_dataset()
    return read_tables(archive_path), version
