import pandas as pd
import pytest

from transform import (
    clean_text_columns,
    clean_integer_columns,
    remove_exact_duplicates,
    validate_primary_key,
    create_theme_groups,
    validate_set_themes,
    add_decade,
    clean_minifig_flag,
    fill_missing_names,
    print_quality_summary,
)


def test_clean_text_columns():
    df = pd.DataFrame({"name": ["  LEGO   City  ", "   "]})

    result = clean_text_columns(df)

    assert result.loc[0, "name"] == "LEGO City"
    assert pd.isna(result.loc[1, "name"])


def test_clean_integer_columns():
    df = pd.DataFrame({"count": ["12", "0", "-3", "2.5", "bad"]})

    result = clean_integer_columns(df, ["count"])

    assert result.loc[0, "count"] == 12
    assert result.loc[1, "count"] == 0
    assert result["count"].iloc[2:].isna().all()
    assert str(result["count"].dtype) == "Int64"


def test_remove_exact_duplicates():
    df = pd.DataFrame({"id": [1, 1, 2]})

    result = remove_exact_duplicates(df)

    assert result["id"].tolist() == [1, 2]


def test_validate_primary_key():
    validate_primary_key(pd.DataFrame({"id": [1, 2]}), "id")

    with pytest.raises(ValueError):
        validate_primary_key(pd.DataFrame({"id": [1, 1]}), "id")

    with pytest.raises(ValueError):
        validate_primary_key(pd.DataFrame({"id": [1, None]}), "id")


def test_create_theme_groups():
    df = pd.DataFrame({
        "theme_name": ["Creator", "Creator", "Creator"],
        "parent_theme": ["Seasonal", "Seasonal", "Other"],
    })

    result = create_theme_groups(df)

    assert len(result) == 2
    assert result["parent_theme"].tolist() == ["Seasonal", "Other"]


def test_validate_set_themes():
    sets = pd.DataFrame({
        "set_num": ["001-1", "002-1"],
        "theme_name": ["City", "City"],
        "parent_theme": ["City", "City"],
    })

    groups = pd.DataFrame({
        "theme_name": ["City"],
        "parent_theme": ["City"],
    })

    result = validate_set_themes(sets, groups)

    assert len(result) == 2
    assert "_merge" not in result.columns

    sets.loc[0, "theme_name"] = "Unknown theme"

    with pytest.raises(ValueError):
        validate_set_themes(sets, groups)


def test_add_decade():
    df = pd.DataFrame({
        "year": pd.Series([1975, 2025, None], dtype="Int64")
    })

    result = add_decade(df)

    assert result.loc[0, "decade"] == 1970
    assert result.loc[1, "decade"] == 2020
    assert pd.isna(result.loc[2, "decade"])


def test_clean_minifig_flag():
    df = pd.DataFrame({
        "num_minifigs": pd.Series([0, 3, None], dtype="Int64"),
        "has_minifigs": ["true", "false", "true"],
    })

    result = clean_minifig_flag(df)

    assert result["has_minifigs"].tolist() == [False, True, True]


def test_fill_missing_names():
    df = pd.DataFrame({"name": [None, "Death Star"]})

    result = fill_missing_names(df)

    assert result["name"].tolist() == ["Unknown", "Death Star"]


def test_quality_summary_reports_values_coerced_to_null(capsys):
    original = pd.DataFrame({
        "name": [None, "Set"],
        "num_parts": ["invalid", "7"],
    })
    cleaned = fill_missing_names(
        clean_integer_columns(original, ["num_parts"])
    )

    print_quality_summary({"sets": original}, {"sets": cleaned})

    output = capsys.readouterr().out
    assert "name: 1 missing in source, 0 null after cleaning" in output
    assert "num_parts: 0 missing in source, 1 null after cleaning" in output