# PEP 723 Inline Dependency Examples

This reference provides practical examples of self-contained Python scripts using PEP 723 inline dependency metadata.

## PEP 723 Basics

PEP 723 allows scripts to declare dependencies directly in comments:

```python
# /// script
# dependencies = [
#   "package-name",
#   "another-package>=1.0.0"
# ]
# ///
```

**Key points:**
- Must use exact comment format: `# ///` markers
- `dependencies` is a list of PEP 508 dependency specifiers
- Optional: specify `requires-python` for minimum Python version
- Running `uv run script.py` automatically creates an isolated environment

## Example 1: Web Scraping Script

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "requests>=2.31.0",
#   "beautifulsoup4>=4.12.0",
#   "lxml>=5.0.0"
# ]
# requires-python = ">=3.10"
# ///

"""
Scrapes article titles from a news website.
Usage: uv run scrape_news.py <url>
"""

import sys
import requests
from bs4 import BeautifulSoup

def scrape_titles(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, 'lxml')
    titles = [h2.get_text(strip=True) for h2 in soup.find_all('h2')]

    return titles

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run scrape_news.py <url>")
        sys.exit(1)

    url = sys.argv[1]
    titles = scrape_titles(url)

    for i, title in enumerate(titles, 1):
        print(f"{i}. {title}")
```

**Run it:**
```bash
uv run scrape_news.py https://example.com
```

## Example 2: Data Analysis Script

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "pandas>=2.0.0",
#   "matplotlib>=3.7.0",
#   "seaborn>=0.13.0"
# ]
# ///

"""
Analyzes CSV data and generates visualizations.
Usage: uv run analyze_data.py data.csv
"""

import sys
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

def analyze_csv(filepath):
    # Read data
    df = pd.read_csv(filepath)

    # Summary statistics
    print("Dataset Summary:")
    print(df.describe())
    print(f"\nShape: {df.shape}")

    # Correlation heatmap
    plt.figure(figsize=(10, 8))
    sns.heatmap(df.corr(), annot=True, cmap='coolwarm', center=0)
    plt.title('Correlation Heatmap')
    plt.tight_layout()
    plt.savefig('correlation_heatmap.png')
    print("\nSaved correlation_heatmap.png")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run analyze_data.py data.csv")
        sys.exit(1)

    analyze_csv(sys.argv[1])
```

## Example 3: API Client Script

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "httpx>=0.27.0",
#   "rich>=13.0.0"
# ]
# ///

"""
Fetches and displays GitHub repository information.
Usage: uv run github_info.py owner/repo
"""

import sys
import httpx
from rich.console import Console
from rich.table import Table

def get_repo_info(repo_full_name):
    url = f"https://api.github.com/repos/{repo_full_name}"

    with httpx.Client() as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()

def display_repo_info(data):
    console = Console()

    table = Table(title=f"Repository: {data['full_name']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Description", data.get('description', 'N/A'))
    table.add_row("Stars", str(data['stargazers_count']))
    table.add_row("Forks", str(data['forks_count']))
    table.add_row("Language", data.get('language', 'N/A'))
    table.add_row("License", data.get('license', {}).get('name', 'N/A'))
    table.add_row("Created", data['created_at'][:10])
    table.add_row("Updated", data['updated_at'][:10])

    console.print(table)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run github_info.py owner/repo")
        sys.exit(1)

    repo_info = get_repo_info(sys.argv[1])
    display_repo_info(repo_info)
```

**Run it:**
```bash
uv run github_info.py astral-sh/uv
```

## Example 4: CLI Tool with Argument Parsing

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "typer>=0.12.0",
#   "rich>=13.0.0"
# ]
# ///

"""
File organization utility with CLI interface.
"""

from pathlib import Path
import typer
from rich.console import Console

app = typer.Typer()
console = Console()

@app.command()
def organize(
    directory: Path = typer.Argument(..., help="Directory to organize"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without moving files")
):
    """
    Organize files by extension into subdirectories.
    """
    if not directory.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)

    # Group files by extension
    files_by_ext = {}
    for file in directory.iterdir():
        if file.is_file():
            ext = file.suffix.lower() or 'no_extension'
            files_by_ext.setdefault(ext, []).append(file)

    # Organize files
    for ext, files in files_by_ext.items():
        target_dir = directory / ext.lstrip('.')

        if dry_run:
            console.print(f"[yellow]Would create:[/yellow] {target_dir}")
            for file in files:
                console.print(f"  [cyan]Would move:[/cyan] {file.name}")
        else:
            target_dir.mkdir(exist_ok=True)
            for file in files:
                file.rename(target_dir / file.name)
            console.print(f"[green]Organized {len(files)} files into {target_dir}[/green]")

if __name__ == "__main__":
    app()
```

**Run it:**
```bash
uv run organize_files.py ~/Downloads --dry-run
```

## Example 5: Testing with Dependencies

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "pytest>=8.0.0",
#   "hypothesis>=6.0.0"
# ]
# ///

"""
Self-testing script with inline tests.
Run: uv run test_utils.py
"""

# Production code
def fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

def is_prime(n: int) -> bool:
    """Check if a number is prime."""
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True

# Tests
import pytest
from hypothesis import given, strategies as st

class TestFibonacci:
    def test_base_cases(self):
        assert fibonacci(0) == 0
        assert fibonacci(1) == 1

    def test_sequence(self):
        assert fibonacci(5) == 5
        assert fibonacci(10) == 55

    @given(st.integers(min_value=0, max_value=20))
    def test_non_negative(self, n):
        assert fibonacci(n) >= 0

class TestPrime:
    def test_known_primes(self):
        primes = [2, 3, 5, 7, 11, 13]
        for p in primes:
            assert is_prime(p)

    def test_known_non_primes(self):
        non_primes = [0, 1, 4, 6, 8, 9, 10]
        for n in non_primes:
            assert not is_prime(n)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**Run tests:**
```bash
uv run test_utils.py
```

## Example 6: Image Processing

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "pillow>=10.0.0",
#   "click>=8.0.0"
# ]
# ///

"""
Batch image resizer.
Usage: uv run resize_images.py input_dir output_dir --width 800
"""

from pathlib import Path
import click
from PIL import Image

@click.command()
@click.argument('input_dir', type=click.Path(exists=True, path_type=Path))
@click.argument('output_dir', type=click.Path(path_type=Path))
@click.option('--width', default=800, help='Target width in pixels')
@click.option('--quality', default=85, help='JPEG quality (1-100)')
def resize_images(input_dir, output_dir, width, quality):
    """Resize all images in INPUT_DIR and save to OUTPUT_DIR."""
    output_dir.mkdir(parents=True, exist_ok=True)

    image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
    images = [f for f in input_dir.iterdir()
              if f.suffix.lower() in image_extensions]

    for img_path in images:
        with Image.open(img_path) as img:
            # Calculate proportional height
            aspect_ratio = img.height / img.width
            new_height = int(width * aspect_ratio)

            # Resize
            resized = img.resize((width, new_height), Image.Resampling.LANCZOS)

            # Save
            output_path = output_dir / img_path.name
            resized.save(output_path, quality=quality, optimize=True)
            click.echo(f"Resized: {img_path.name} -> {output_path}")

    click.echo(f"\nProcessed {len(images)} images")

if __name__ == "__main__":
    resize_images()
```

## Example 7: Database Query Tool

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "sqlalchemy>=2.0.0",
#   "pandas>=2.0.0",
#   "tabulate>=0.9.0"
# ]
# ///

"""
Quick database query tool.
Usage: uv run db_query.py "SELECT * FROM users LIMIT 10"
"""

import sys
from sqlalchemy import create_engine, text
import pandas as pd

def query_db(sql: str, db_url: str = "sqlite:///example.db"):
    """Execute SQL query and display results as a table."""
    engine = create_engine(db_url)

    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    print(df.to_markdown(index=False, tablefmt='grid'))
    print(f"\nRows: {len(df)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run db_query.py 'SELECT ...'")
        sys.exit(1)

    sql = sys.argv[1]
    query_db(sql)
```

## Example 8: JSON/YAML Converter

```python
#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "pyyaml>=6.0.0",
#   "rich>=13.0.0"
# ]
# ///

"""
Convert between JSON and YAML formats.
Usage: uv run convert.py input.json output.yaml
"""

import sys
import json
from pathlib import Path
import yaml
from rich.console import Console

console = Console()

def convert_file(input_path: Path, output_path: Path):
    """Convert between JSON and YAML based on file extensions."""
    # Read input
    with open(input_path) as f:
        if input_path.suffix == '.json':
            data = json.load(f)
        elif input_path.suffix in {'.yaml', '.yml'}:
            data = yaml.safe_load(f)
        else:
            console.print(f"[red]Unsupported input format: {input_path.suffix}[/red]")
            sys.exit(1)

    # Write output
    with open(output_path, 'w') as f:
        if output_path.suffix == '.json':
            json.dump(data, f, indent=2)
        elif output_path.suffix in {'.yaml', '.yml'}:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        else:
            console.print(f"[red]Unsupported output format: {output_path.suffix}[/red]")
            sys.exit(1)

    console.print(f"[green]✓[/green] Converted {input_path} → {output_path}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        console.print("Usage: uv run convert.py input.json output.yaml")
        sys.exit(1)

    convert_file(Path(sys.argv[1]), Path(sys.argv[2]))
```

## Advanced Features

### Specifying Python Version

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["package"]
# ///
```

### Platform-Specific Dependencies

```python
# /// script
# dependencies = [
#   "package",
#   "windows-only; sys_platform == 'win32'",
#   "linux-only; sys_platform == 'linux'"
# ]
# ///
```

### Package Extras

```python
# /// script
# dependencies = [
#   "fastapi[all]>=0.100.0",
#   "sqlalchemy[asyncio]>=2.0.0"
# ]
# ///
```

### Version Constraints

```python
# /// script
# dependencies = [
#   "package>=1.0.0,<2.0.0",  # Compatible version range
#   "another~=1.4.0",          # ~= 1.4.0 means >=1.4.0, <1.5.0
#   "exact==2.3.1"             # Exact version pin
# ]
# ///
```

## Best Practices

1. **Always specify version constraints** to ensure reproducibility:
   ```python
   # Good
   # dependencies = ["requests>=2.31.0"]

   # Avoid
   # dependencies = ["requests"]
   ```

2. **Use the shebang for executable scripts**:
   ```python
   #!/usr/bin/env -S uv run
   ```
   Then: `chmod +x script.py && ./script.py`

3. **Add docstrings** explaining usage:
   ```python
   """
   Script description here.
   Usage: uv run script.py <arguments>
   """
   ```

4. **Keep scripts focused** - inline dependencies work best for single-file utilities

5. **For multi-file projects**, use `uv init` and `pyproject.toml` instead

## Migration Path

**Before (separate requirements.txt):**
```
project/
├── script.py
└── requirements.txt
```

**After (PEP 723):**
```
project/
└── script.py  (with inline dependencies)
```

**Migrating:**
```bash
# Read requirements.txt, add to script as PEP 723
cat requirements.txt  # Copy package names
# Edit script.py and add:
# /// script
# dependencies = [
#   "package1>=1.0.0",
#   "package2>=2.0.0"
# ]
# ///
```

## Troubleshooting

**Error: "Invalid script metadata"**
- Check that `# ///` markers are exact (no extra spaces)
- Ensure `dependencies` is a valid TOML list

**Dependencies not installing:**
- Run `uv cache clean` to clear cache
- Verify dependency names on PyPI

**Script runs with wrong Python:**
- Add `requires-python = ">=3.X"` to metadata
- Or: `uv run --python 3.12 script.py`

## Further Reading

- PEP 723 Specification: https://peps.python.org/pep-0723/
- uv Scripts Guide: https://docs.astral.sh/uv/guides/scripts/
- Dependency Specifiers (PEP 508): https://peps.python.org/pep-0508/
