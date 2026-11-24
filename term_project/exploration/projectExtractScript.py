# Install dependencies as needed:
# pip install kagglehub

import kagglehub
from kagglehub import KaggleDatasetAdapter

# Set to specific file (there are 3 available in the dataset)
file_path = "lego_minifigures_1975_2025.csv"

df = kagglehub.load_dataset(
  KaggleDatasetAdapter.PANDAS,
  "abdurrahmanhakanacar/lego-sets-themes-and-minifigures-1975-2025",
  file_path,
)

print("First 5 records:", df.head())