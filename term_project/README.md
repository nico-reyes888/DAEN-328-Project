#Lego Term Project

##Transform Nicolas Reyes



Add pandas and pytest to requirements.txt.

After Extract creates the three DataFrames, run:

python
from transform import transform_data, print_quality_summary

cleaned = transform_data(sets, themes, minifigures)

print_quality_summary(
    {"sets": sets, "themes": themes, "minifigures": minifigures},
    cleaned
)
For Load: The output contains sets, themes, minifigures, and theme_groups.

For Load and Dashboard: Join sets to theme_groups using both theme_name and parent_theme. Joining directly to themes can duplicate sets.

For Docker: Use transform.py and include the tests folder. The notebook is only to explore 

All 9 tests passed locally. Still needs docker integration.

