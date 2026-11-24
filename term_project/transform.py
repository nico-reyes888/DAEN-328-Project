import pandas as pd
from builtins import ValueError


def clean_text_columns(df):
    cleaned = df.copy()

    text_columns = cleaned.select_dtypes(
        include=["object", "string"]
    ).columns

    for column in text_columns:
        cleaned[column] = (
            cleaned[column]
            .astype("string")
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .replace("", pd.NA)
        )

    return cleaned

def clean_integer_columns(df, columns):
    cleaned = df.copy()

    for column in columns:
        numbers = pd.to_numeric(cleaned[column], errors="coerce")

        valid = (
            numbers.notna()
            & (numbers >= 0)
            & (numbers < 2**63)
            & (numbers % 1 == 0)
        )

        cleaned[column] = numbers.where(valid).astype("Int64")

    return cleaned

def remove_exact_duplicates(df):
    return df.drop_duplicates().reset_index(drop=True)


def validate_primary_key(df, key):
    if df[key].isna().any():
        raise ValueError(f"Missing IDs in {key}")

    if df[key].duplicated().any():
        raise ValueError(f"Conflicting duplicate IDs in {key}")


def create_theme_groups(df):
    groups = df[["theme_name", "parent_theme"]].copy()

    if groups.isna().any().any():
        raise ValueError("Missing theme labels")

    groups = groups.drop_duplicates().reset_index(drop=True)

    return groups

def validate_set_themes(sets_df, groups_df):
    columns = ["theme_name", "parent_theme"]

    if sets_df[columns].isna().any().any():
        raise ValueError("Missing theme labels in sets")

    checked = sets_df.merge(
        groups_df,
        on=columns,
        how="left",
        validate="many_to_one",
        indicator=True
    )

    if (checked["_merge"] == "left_only").any():
        raise ValueError("Some sets have no matching theme group")

    return checked.drop(columns="_merge")
def add_decade(df):
    cleaned = df.copy()
    cleaned["decade"] = (cleaned["year"] // 10) * 10
    return cleaned

def clean_minifig_flag(df):
    cleaned = df.copy()

    cleaned["has_minifigs"] = (
        cleaned["has_minifigs"]
        .astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False})
        .astype("boolean")
    )

    known = cleaned["num_minifigs"].notna()

    cleaned.loc[known, "has_minifigs"] = (
        cleaned.loc[known, "num_minifigs"] > 0
    )

    return cleaned
def fill_missing_names(df):
    cleaned = df.copy()
    cleaned["name"] = cleaned["name"].fillna("Unknown")
    return cleaned


def transform_data(sets, themes, minifigures):
    # Clean text.
    sets_clean = clean_text_columns(sets)
    themes_clean = clean_text_columns(themes)
    minifigures_clean = clean_text_columns(minifigures)

    # Convert and validate numeric columns.
    sets_clean = clean_integer_columns(
        sets_clean,
        ["year", "num_parts", "num_minifigs", "num_unique_colors"]
    )

    themes_clean = clean_integer_columns(
        themes_clean,
        ["id", "total_sets"]
    )

    minifigures_clean = clean_integer_columns(
        minifigures_clean,
        ["num_parts", "total_sets_appeared"]
    )

    # check missing values  handle them, fill missing names, and clean minifig flag.
    sets_clean = fill_missing_names(sets_clean)
    minifigures_clean = fill_missing_names(minifigures_clean)
    sets_clean = clean_minifig_flag(sets_clean)

    # Remove exact duplicates.
    sets_clean = remove_exact_duplicates(sets_clean)
    themes_clean = remove_exact_duplicates(themes_clean)
    minifigures_clean = remove_exact_duplicates(minifigures_clean)

    # Validate primary keys.
    validate_primary_key(sets_clean, "set_num")
    validate_primary_key(themes_clean, "id")
    validate_primary_key(minifigures_clean, "fig_num")

    # Validate theme relationships and calculate decades.
    theme_groups = create_theme_groups(themes_clean)
    sets_clean = validate_set_themes(sets_clean, theme_groups)
    sets_clean = add_decade(sets_clean)

    return {
        "sets": sets_clean,
        "themes": themes_clean,
        "minifigures": minifigures_clean,
        "theme_groups": theme_groups
    }
def print_quality_summary(original_tables, cleaned_tables):
    numeric_columns = {
        "sets": {"year", "num_parts", "num_minifigs", "num_unique_colors"},
        "themes": {"id", "total_sets"},
        "minifigures": {"num_parts", "total_sets_appeared"},
    }

    for name, original in original_tables.items():
        cleaned = cleaned_tables[name]
        normalized_original = clean_text_columns(original)

        print(f"\n{name}")
        print("Rows received:", len(original))
        print("Rows after cleaning:", len(cleaned))
        print("Duplicates removed:", len(original) - len(cleaned))

        for column in original.columns:
            missing_before = int(normalized_original[column].isna().sum())
            missing_after = int(cleaned[column].isna().sum())

            if column == "name":
                action = "filled with Unknown"
            elif column in [
                "set_num", "fig_num", "id",
                "theme_name", "parent_theme"
            ]:
                action = "required; missing or duplicate values stop the run"
            elif column in numeric_columns.get(name, set()):
                action = "invalid numeric values converted to null; source nulls kept"
            elif column == "has_minifigs":
                action = "derived from num_minifigs when known; otherwise parsed"
            else:
                action = "source nulls kept unless derived"

            print(
                f"{column}: {missing_before} missing in source, "
                f"{missing_after} null after cleaning — {action}"
            )
