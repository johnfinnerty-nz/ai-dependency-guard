import unittest

from ai_dependency_guard.extractors import extract_references


class ExtractorTests(unittest.TestCase):
    def test_extracts_npm_install_references_with_line_numbers(self):
        content = """# Setup
npm install express definitely-not-a-real-package-12345
npx create-vite@latest demo
"""

        references = extract_references("README.md", content)

        self.assertEqual(
            [(item.ecosystem, item.name, item.line) for item in references],
            [
                ("npm", "express", 2),
                ("npm", "definitely-not-a-real-package-12345", 2),
                ("npm", "create-vite", 3),
            ],
        )

    def test_extracts_single_package_commands_after_flags(self):
        content = """npx --yes definitely-not-a-real-package-12345
pnpm dlx --silent definitely-not-a-real-package-67890
"""

        references = extract_references("AGENTS.md", content)

        self.assertEqual(
            [(item.ecosystem, item.name, item.line) for item in references],
            [
                ("npm", "definitely-not-a-real-package-12345", 1),
                ("npm", "definitely-not-a-real-package-67890", 2),
            ],
        )

    def test_extracts_requirements_and_skips_comments_and_options(self):
        content = """# runtime
requests>=2.31
--index-url https://example.invalid/simple
definitely-not-a-real-python-package-12345==1.0
"""

        references = extract_references("requirements.txt", content)

        self.assertEqual(
            [(item.ecosystem, item.name, item.line) for item in references],
            [
                ("pypi", "requests", 2),
                ("pypi", "definitely-not-a-real-python-package-12345", 4),
            ],
        )

    def test_extracts_package_json_dependencies(self):
        content = """{
  "name": "demo",
  "dependencies": {
    "requests": "^1.0.0",
    "definitely-not-a-real-package-12345": "^1.0.0"
  },
  "devDependencies": {
    "pytest": "^8.0.0"
  }
}
"""

        references = extract_references("package.json", content)

        self.assertEqual(
            [(item.ecosystem, item.name, item.line) for item in references],
            [
                ("npm", "requests", 4),
                ("npm", "definitely-not-a-real-package-12345", 5),
                ("npm", "pytest", 8),
            ],
        )

    def test_skips_local_and_git_install_targets(self):
        content = """npm install ./local-package git+https://github.com/example/project.git
pip install -e ./local-package git+https://github.com/example/project.git
"""

        references = extract_references("script.sh", content)

        self.assertEqual(references, [])

    def test_skips_requirement_file_arguments_for_pip(self):
        references = extract_references(
            "README.md",
            "pip install -r requirements.txt\npip install --requirement=dev.txt requests\n",
        )

        self.assertEqual(
            [(reference.ecosystem, reference.name) for reference in references],
            [("pypi", "requests")],
        )

    def test_extracts_pyproject_project_and_poetry_dependencies(self):
        content = """[project]
dependencies = [
  "requests>=2.31",
  "definitely-not-a-real-python-package-12345"
]

[tool.poetry.dependencies]
python = ">=3.11"
httpx = "^0.27"
"""

        references = extract_references("pyproject.toml", content)

        self.assertEqual(
            [(item.ecosystem, item.name, item.line) for item in references],
            [
                ("pypi", "requests", 3),
                ("pypi", "definitely-not-a-real-python-package-12345", 4),
                ("pypi", "httpx", 9),
            ],
        )

    def test_extracts_inline_package_json_dependencies(self):
        content = '{"dependencies":{"definitely-not-a-real-package-12345":"1.0.0"}}\n'

        references = extract_references("package.json", content)

        self.assertEqual(
            [(item.name, item.line) for item in references],
            [("definitely-not-a-real-package-12345", 1)],
        )

    def test_extracts_go_requirements_and_skips_local_replacements(self):
        content = """module example.com/app

require github.com/stretchr/testify v1.9.0

require (
    // test helper
    golang.org/x/tools v0.24.0
    example.com/local v0.0.0
)

replace example.com/local => ../local
"""

        references = extract_references("go.mod", content)

        self.assertEqual(
            [
                (item.ecosystem, item.name, item.version, item.line, item.source)
                for item in references
            ],
            [
                ("go", "github.com/stretchr/testify", "v1.9.0", 3, "require"),
                ("go", "golang.org/x/tools", "v0.24.0", 7, "require"),
            ],
        )

    def test_rejects_malformed_go_requirements(self):
        with self.assertRaisesRegex(ValueError, "Malformed module requirement"):
            extract_references("go.mod", "require github.com/example/module\n")


if __name__ == "__main__":
    unittest.main()
