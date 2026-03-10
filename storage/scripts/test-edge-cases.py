#!/usr/bin/env python3
"""
Storage System Edge Case Testing (SCR-0002)
Tests edge cases and boundary conditions in the storage system.

Tests performed:
1. Malformed JSON in index files
2. Extremely long descriptions (10K+ characters)
3. Special characters in names/descriptions (unicode, quotes, backslashes)
4. IDs out of sequence
5. Category lists referencing IDs not in main array
6. Missing or wrong version field
"""

import json
import os
import sys
import copy
import tempfile
import shutil
from pathlib import Path

STORAGE_ROOT = Path(__file__).resolve().parent.parent

# Import the validator so we can reuse it
sys.path.insert(0, str(STORAGE_ROOT / "scripts"))
from importlib import util as importutil

# Load validate-storage module
spec = importutil.spec_from_file_location("validate_storage",
    STORAGE_ROOT / "scripts" / "validate-storage.py")
validate_mod = importutil.module_from_spec(spec)
spec.loader.exec_module(validate_mod)


class EdgeCaseResult:
    def __init__(self, test_name, severity, passed, details):
        self.test_name = test_name
        self.severity = severity  # critical, high, medium, low
        self.passed = passed  # True = system handled it correctly
        self.details = details

    def __str__(self):
        status = "HANDLED" if self.passed else "VULNERABILITY"
        return f"[{self.severity.upper():8s}] {status:13s} | {self.test_name}: {self.details}"


class EdgeCaseTester:
    def __init__(self):
        self.results = []
        self.temp_dir = None

    def setup_temp_storage(self):
        """Create a temporary copy of the storage directory for testing."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="storage_test_"))
        # Copy storage structure
        for subdir in ["scripts", "api-tools", "sources"]:
            src = STORAGE_ROOT / subdir
            dst = self.temp_dir / subdir
            shutil.copytree(src, dst)
        # Copy DIRECTIONS.md
        shutil.copy2(STORAGE_ROOT / "DIRECTIONS.md", self.temp_dir / "DIRECTIONS.md")
        return self.temp_dir

    def cleanup(self):
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            self.temp_dir = None

    def run_validator_on(self, temp_root):
        """Run the validator against a temporary storage root."""
        # Monkey-patch STORAGE_ROOT in the validator module
        original_root = validate_mod.STORAGE_ROOT
        validate_mod.STORAGE_ROOT = temp_root
        result = validate_mod.ValidationResult()
        try:
            validate_mod.validate_scripts(result)
            validate_mod.validate_api_tools(result)
            validate_mod.validate_sources(result)
        finally:
            validate_mod.STORAGE_ROOT = original_root
        return result

    def test_malformed_json(self):
        """Test 1: Malformed JSON in index files."""
        print("\n--- Test 1: Malformed JSON ---")

        malformed_cases = [
            ("truncated JSON", '{"version": "1.0", "scripts": ['),
            ("trailing comma", '{"version": "1.0", "scripts": [],}'),
            ("single quotes", "{'version': '1.0'}"),
            ("no quotes on keys", '{version: "1.0"}'),
            ("empty file", ''),
            ("just whitespace", '   \n\t  '),
            ("binary-like content", '\x00\x01\x02\x03'),
            ("HTML instead of JSON", '<html><body>Not JSON</body></html>'),
        ]

        for case_name, bad_content in malformed_cases:
            temp = self.setup_temp_storage()
            try:
                # Write malformed JSON to scripts index
                with open(temp / "scripts" / "index.json", "w") as f:
                    f.write(bad_content)

                vr = self.run_validator_on(temp)

                if vr.errors:
                    self.results.append(EdgeCaseResult(
                        f"Malformed JSON ({case_name})",
                        "critical",
                        True,
                        f"Validator caught the error: {vr.errors[0]['message'][:80]}"
                    ))
                else:
                    self.results.append(EdgeCaseResult(
                        f"Malformed JSON ({case_name})",
                        "critical",
                        False,
                        "Validator did NOT catch the malformed JSON!"
                    ))
            except Exception as e:
                self.results.append(EdgeCaseResult(
                    f"Malformed JSON ({case_name})",
                    "critical",
                    False,
                    f"Unhandled exception: {type(e).__name__}: {str(e)[:80]}"
                ))
            finally:
                self.cleanup()

    def test_long_descriptions(self):
        """Test 2: Extremely long descriptions (10K+ characters)."""
        print("\n--- Test 2: Long Descriptions ---")

        temp = self.setup_temp_storage()
        try:
            long_desc = "A" * 10000
            very_long_desc = "B" * 100000

            # Create a script entry with a very long description
            index_data = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [
                    {
                        "id": "SCR-0001",
                        "name": "long-desc-test",
                        "filename": "long-desc-test.py",
                        "language": "python",
                        "category": "utility",
                        "description": long_desc,
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    },
                    {
                        "id": "SCR-0002",
                        "name": "very-long-desc-test",
                        "filename": "very-long-desc-test.py",
                        "language": "python",
                        "category": "utility",
                        "description": very_long_desc,
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "categories": {
                    "data-collection": [],
                    "analysis": [],
                    "automation": [],
                    "utility": ["SCR-0001", "SCR-0002"]
                },
                "language_index": {}
            }

            # Create the referenced files
            (temp / "scripts" / "long-desc-test.py").write_text("# test")
            (temp / "scripts" / "very-long-desc-test.py").write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr = self.run_validator_on(temp)

            # Check if the file size is unreasonable
            file_size = (temp / "scripts" / "index.json").stat().st_size

            if file_size > 50000:
                self.results.append(EdgeCaseResult(
                    "Long description (10K chars)",
                    "medium",
                    False,
                    f"Validator accepts 10K descriptions without warning. Index file grew to {file_size} bytes."
                ))
            else:
                self.results.append(EdgeCaseResult(
                    "Long description (10K chars)",
                    "medium",
                    True,
                    f"Description accepted, file size reasonable ({file_size} bytes)."
                ))

            # Check for warnings about length
            has_length_warning = any("long" in w["message"].lower() or "length" in w["message"].lower()
                                     for w in vr.warnings)
            self.results.append(EdgeCaseResult(
                "Long description warning",
                "low",
                has_length_warning,
                "Validator warns about long descriptions" if has_length_warning
                else "Validator does NOT warn about extremely long descriptions (100K chars). No max length enforced."
            ))
        except Exception as e:
            self.results.append(EdgeCaseResult(
                "Long description",
                "medium",
                False,
                f"Unhandled exception: {type(e).__name__}: {str(e)[:100]}"
            ))
        finally:
            self.cleanup()

    def test_special_characters(self):
        """Test 3: Special characters in names/descriptions."""
        print("\n--- Test 3: Special Characters ---")

        special_cases = [
            ("unicode emoji", "Stock Analyzer \U0001F4C8\U0001F4B0", "Analyzes stocks with \u00E9\u00E8\u00EA accents"),
            ("quotes", 'Script "with" quotes', "Description with 'single' and \"double\" quotes"),
            ("backslashes", "path\\to\\script", "Uses C:\\Users\\data\\file.csv paths"),
            ("newlines in desc", "normal-name", "Line 1\nLine 2\nLine 3"),
            ("null bytes", "null-test", "Before\x00After"),
            ("html tags", "html-test", "<script>alert('xss')</script>"),
            ("json special", "json-special", '{"key": "value"}'),
            ("very long name", "A" * 500, "normal description"),
            ("unicode CJK", "\u4e2d\u6587\u811a\u672c", "\u65e5\u672c\u8a9e\u306e\u8aac\u660e"),
            ("RTL text", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629", "\u0639\u0631\u0628\u064a script"),
        ]

        for case_name, name, desc in special_cases:
            temp = self.setup_temp_storage()
            try:
                index_data = {
                    "version": "1.0",
                    "last_updated": "2026-03-10",
                    "scripts": [
                        {
                            "id": "SCR-0001",
                            "name": name,
                            "filename": "test-special.py",
                            "language": "python",
                            "category": "utility",
                            "description": desc,
                            "dependencies": [],
                            "added": "2026-03-10",
                            "added_by_team": "TEAM-0003"
                        }
                    ],
                    "categories": {
                        "data-collection": [],
                        "analysis": [],
                        "automation": [],
                        "utility": ["SCR-0001"]
                    },
                    "language_index": {}
                }

                (temp / "scripts" / "test-special.py").write_text("# test")

                with open(temp / "scripts" / "index.json", "w", encoding="utf-8") as f:
                    json.dump(index_data, f, ensure_ascii=False)

                # Try to read it back
                with open(temp / "scripts" / "index.json", "r", encoding="utf-8") as f:
                    readback = json.load(f)

                vr = self.run_validator_on(temp)

                roundtrip_ok = readback["scripts"][0]["name"] == name

                self.results.append(EdgeCaseResult(
                    f"Special chars ({case_name})",
                    "medium" if not roundtrip_ok else "low",
                    roundtrip_ok,
                    f"Roundtrip OK, validator errors={len(vr.errors)}, warnings={len(vr.warnings)}"
                    if roundtrip_ok else f"Roundtrip FAILED for {case_name}"
                ))
            except Exception as e:
                self.results.append(EdgeCaseResult(
                    f"Special chars ({case_name})",
                    "medium",
                    False,
                    f"Exception: {type(e).__name__}: {str(e)[:80]}"
                ))
            finally:
                self.cleanup()

    def test_ids_out_of_sequence(self):
        """Test 4: IDs out of sequence."""
        print("\n--- Test 4: IDs Out of Sequence ---")

        temp = self.setup_temp_storage()
        try:
            index_data = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [
                    {
                        "id": "SCR-0005",
                        "name": "skipped-id",
                        "filename": "skipped.py",
                        "language": "python",
                        "category": "utility",
                        "description": "ID starts at 5 instead of 1",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    },
                    {
                        "id": "SCR-0002",
                        "name": "out-of-order",
                        "filename": "out-of-order.py",
                        "language": "python",
                        "category": "analysis",
                        "description": "ID 2 appears after ID 5",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    },
                    {
                        "id": "SCR-9999",
                        "name": "high-id",
                        "filename": "high-id.py",
                        "language": "python",
                        "category": "automation",
                        "description": "Very high ID number",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "categories": {
                    "data-collection": [],
                    "analysis": ["SCR-0002"],
                    "automation": ["SCR-9999"],
                    "utility": ["SCR-0005"]
                },
                "language_index": {}
            }

            for fname in ["skipped.py", "out-of-order.py", "high-id.py"]:
                (temp / "scripts" / fname).write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr = self.run_validator_on(temp)

            has_sequence_warning = any("sequence" in w["message"].lower() or "order" in w["message"].lower()
                                       for w in vr.warnings)
            has_gap_warning = any("gap" in w["message"].lower() or "skip" in w["message"].lower()
                                  for w in vr.warnings)

            self.results.append(EdgeCaseResult(
                "IDs out of sequence",
                "low",
                True,  # Out-of-order IDs are technically valid
                f"Validator accepts out-of-sequence IDs. Errors={len(vr.errors)}, Warnings={len(vr.warnings)}. "
                f"Sequence warning: {has_sequence_warning}, Gap warning: {has_gap_warning}"
            ))

            # Test ID with wrong prefix
            index_data["scripts"].append({
                "id": "TOOL-0001",  # Wrong prefix for scripts
                "name": "wrong-prefix",
                "filename": "wrong-prefix.py",
                "language": "python",
                "category": "utility",
                "description": "Has TOOL prefix in scripts",
                "dependencies": [],
                "added": "2026-03-10",
                "added_by_team": "TEAM-0003"
            })
            index_data["categories"]["utility"].append("TOOL-0001")
            (temp / "scripts" / "wrong-prefix.py").write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr2 = self.run_validator_on(temp)
            caught_wrong_prefix = any("id-format" in e["check"] for e in vr2.errors)

            self.results.append(EdgeCaseResult(
                "Wrong ID prefix (TOOL- in scripts)",
                "high",
                caught_wrong_prefix,
                "Validator caught wrong ID prefix" if caught_wrong_prefix
                else "Validator did NOT catch wrong ID prefix in scripts!"
            ))
        except Exception as e:
            self.results.append(EdgeCaseResult(
                "IDs out of sequence",
                "medium",
                False,
                f"Exception: {type(e).__name__}: {str(e)[:100]}"
            ))
        finally:
            self.cleanup()

    def test_category_reference_mismatch(self):
        """Test 5: Category lists reference IDs not in main array."""
        print("\n--- Test 5: Category Reference Mismatch ---")

        temp = self.setup_temp_storage()
        try:
            # Case A: Category references a non-existent ID
            index_data = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [],
                "categories": {
                    "data-collection": ["SCR-0099"],  # Does not exist
                    "analysis": [],
                    "automation": [],
                    "utility": []
                },
                "language_index": {}
            }

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr = self.run_validator_on(temp)
            caught_phantom = any("ref-integrity" in e["check"] and "SCR-0099" in e["message"]
                                 for e in vr.errors)

            self.results.append(EdgeCaseResult(
                "Category references phantom ID",
                "high",
                caught_phantom,
                "Validator caught phantom reference" if caught_phantom
                else "Validator did NOT catch phantom reference in category list!"
            ))

            # Case B: Entry exists but is not in its category list
            index_data2 = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [
                    {
                        "id": "SCR-0001",
                        "name": "orphan-entry",
                        "filename": "orphan.py",
                        "language": "python",
                        "category": "utility",
                        "description": "Not listed in utility category",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "categories": {
                    "data-collection": [],
                    "analysis": [],
                    "automation": [],
                    "utility": []  # SCR-0001 missing!
                },
                "language_index": {}
            }
            (temp / "scripts" / "orphan.py").write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data2, f)

            vr2 = self.run_validator_on(temp)
            caught_missing_cat = any("ref-integrity" in e["check"] and "SCR-0001" in e["message"]
                                     for e in vr2.errors)

            self.results.append(EdgeCaseResult(
                "Entry missing from its category list",
                "high",
                caught_missing_cat,
                "Validator caught missing category listing" if caught_missing_cat
                else "Validator did NOT catch entry missing from its category list!"
            ))

            # Case C: Entry listed in WRONG category
            index_data3 = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [
                    {
                        "id": "SCR-0001",
                        "name": "wrong-cat",
                        "filename": "wrong-cat.py",
                        "language": "python",
                        "category": "utility",
                        "description": "Listed in analysis instead of utility",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "categories": {
                    "data-collection": [],
                    "analysis": ["SCR-0001"],  # Wrong category!
                    "automation": [],
                    "utility": []  # Should be here
                },
                "language_index": {}
            }
            (temp / "scripts" / "wrong-cat.py").write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data3, f)

            vr3 = self.run_validator_on(temp)
            caught_wrong_cat = any("ref-integrity" in e["check"] for e in vr3.errors)

            self.results.append(EdgeCaseResult(
                "Entry listed in wrong category",
                "high",
                caught_wrong_cat,
                f"Validator caught wrong category assignment (errors: {len(vr3.errors)})" if caught_wrong_cat
                else "Validator did NOT catch entry in wrong category!"
            ))
        except Exception as e:
            self.results.append(EdgeCaseResult(
                "Category reference mismatch",
                "high",
                False,
                f"Exception: {type(e).__name__}: {str(e)[:100]}"
            ))
        finally:
            self.cleanup()

    def test_version_field_issues(self):
        """Test 6: Missing or wrong version field."""
        print("\n--- Test 6: Version Field Issues ---")

        version_cases = [
            ("missing version", {
                "last_updated": "2026-03-10",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("numeric version", {
                "version": 1.0,
                "last_updated": "2026-03-10",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("empty version", {
                "version": "",
                "last_updated": "2026-03-10",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("future version", {
                "version": "99.0",
                "last_updated": "2026-03-10",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("missing last_updated", {
                "version": "1.0",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("invalid date in last_updated", {
                "version": "1.0",
                "last_updated": "not-a-date",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
            ("impossible date", {
                "version": "1.0",
                "last_updated": "2026-02-30",
                "scripts": [],
                "categories": {
                    "data-collection": [], "analysis": [],
                    "automation": [], "utility": []
                },
                "language_index": {}
            }),
        ]

        for case_name, data in version_cases:
            temp = self.setup_temp_storage()
            try:
                with open(temp / "scripts" / "index.json", "w") as f:
                    json.dump(data, f)

                vr = self.run_validator_on(temp)
                has_issue = len(vr.errors) > 0

                severity = "high" if "missing" in case_name else "medium"
                self.results.append(EdgeCaseResult(
                    f"Version issue ({case_name})",
                    severity,
                    has_issue,
                    f"Validator {'caught' if has_issue else 'MISSED'} the issue. "
                    f"Errors: {len(vr.errors)}, Warnings: {len(vr.warnings)}"
                ))
            except Exception as e:
                self.results.append(EdgeCaseResult(
                    f"Version issue ({case_name})",
                    "medium",
                    False,
                    f"Exception: {type(e).__name__}: {str(e)[:80]}"
                ))
            finally:
                self.cleanup()

    def test_sources_specific_edge_cases(self):
        """Additional edge cases specific to sources subsection."""
        print("\n--- Test 7: Sources-Specific Edge Cases ---")

        temp = self.setup_temp_storage()
        try:
            # Source in index but no SRC-XXXX.json file
            index_data = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "sources": [
                    {
                        "id": "SRC-0001",
                        "name": "Missing File Source",
                        "url": "https://example.com/api",
                        "api_docs_url": "https://example.com/docs",
                        "data_types": ["financial"],
                        "access_type": "free",
                        "auth_method": "none",
                        "rate_limits": "100/min",
                        "response_format": "json",
                        "reliability": "high",
                        "last_verified": "2026-03-10",
                        "notes": "Test source",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "by_data_type": {
                    "financial": ["SRC-0001"],
                    "news": [], "social-media": [], "government": [],
                    "scientific": [], "geospatial": [], "general": []
                },
                "by_access_type": {
                    "free": ["SRC-0001"],
                    "freemium": [], "paid": [], "api-key-required": []
                }
            }

            with open(temp / "sources" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr = self.run_validator_on(temp)
            caught_missing_file = any("file-exists" in e["check"] for e in vr.errors)

            self.results.append(EdgeCaseResult(
                "Source entry without SRC-XXXX.json file",
                "high",
                caught_missing_file,
                "Validator caught missing source file" if caught_missing_file
                else "Validator did NOT check for source file existence!"
            ))

            # Orphan SRC file (file exists but not in index)
            (temp / "sources" / "SRC-0099.json").write_text('{"id": "SRC-0099"}')
            vr2 = self.run_validator_on(temp)
            caught_orphan = any("orphan" in w["check"] for w in vr2.warnings)

            self.results.append(EdgeCaseResult(
                "Orphan SRC-XXXX.json file",
                "medium",
                caught_orphan,
                "Validator caught orphan source file" if caught_orphan
                else "Validator did NOT detect orphan source file!"
            ))
        except Exception as e:
            self.results.append(EdgeCaseResult(
                "Sources edge cases",
                "high",
                False,
                f"Exception: {type(e).__name__}: {str(e)[:100]}"
            ))
        finally:
            self.cleanup()

    def test_duplicate_ids_across_entries(self):
        """Test duplicate IDs within same subsection."""
        print("\n--- Test 8: Duplicate IDs ---")

        temp = self.setup_temp_storage()
        try:
            index_data = {
                "version": "1.0",
                "last_updated": "2026-03-10",
                "scripts": [
                    {
                        "id": "SCR-0001",
                        "name": "first-script",
                        "filename": "first.py",
                        "language": "python",
                        "category": "utility",
                        "description": "First script",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    },
                    {
                        "id": "SCR-0001",  # Duplicate!
                        "name": "second-script",
                        "filename": "second.py",
                        "language": "python",
                        "category": "analysis",
                        "description": "Same ID as first",
                        "dependencies": [],
                        "added": "2026-03-10",
                        "added_by_team": "TEAM-0003"
                    }
                ],
                "categories": {
                    "data-collection": [],
                    "analysis": ["SCR-0001"],
                    "automation": [],
                    "utility": ["SCR-0001"]
                },
                "language_index": {}
            }

            (temp / "scripts" / "first.py").write_text("# test")
            (temp / "scripts" / "second.py").write_text("# test")

            with open(temp / "scripts" / "index.json", "w") as f:
                json.dump(index_data, f)

            vr = self.run_validator_on(temp)
            caught_dup = any("duplicate" in e["check"].lower() or "duplicate" in e["message"].lower()
                             for e in vr.errors)

            self.results.append(EdgeCaseResult(
                "Duplicate IDs in same subsection",
                "critical",
                caught_dup,
                "Validator caught duplicate IDs" if caught_dup
                else "Validator did NOT detect duplicate IDs!"
            ))
        except Exception as e:
            self.results.append(EdgeCaseResult(
                "Duplicate IDs",
                "critical",
                False,
                f"Exception: {type(e).__name__}: {str(e)[:100]}"
            ))
        finally:
            self.cleanup()

    def run_all(self):
        """Run all edge case tests."""
        print("=" * 70)
        print("Storage System Edge Case Testing")
        print("=" * 70)

        self.test_malformed_json()
        self.test_long_descriptions()
        self.test_special_characters()
        self.test_ids_out_of_sequence()
        self.test_category_reference_mismatch()
        self.test_version_field_issues()
        self.test_sources_specific_edge_cases()
        self.test_duplicate_ids_across_entries()

        print("\n" + "=" * 70)
        print("RESULTS SUMMARY")
        print("=" * 70)

        # Group by severity
        by_severity = {"critical": [], "high": [], "medium": [], "low": []}
        handled = 0
        vulnerable = 0

        for r in self.results:
            by_severity[r.severity].append(r)
            if r.passed:
                handled += 1
            else:
                vulnerable += 1

        for severity in ["critical", "high", "medium", "low"]:
            items = by_severity[severity]
            if items:
                print(f"\n{severity.upper()} ({len(items)} tests):")
                for r in items:
                    print(f"  {r}")

        print(f"\n{'=' * 70}")
        print(f"Total: {len(self.results)} tests | Handled: {handled} | Vulnerabilities: {vulnerable}")

        if vulnerable > 0:
            print(f"\nVULNERABILITIES FOUND: {vulnerable} edge cases are not properly handled!")
        else:
            print("\nAll edge cases are properly handled.")

        print("=" * 70)

        return vulnerable == 0


if __name__ == "__main__":
    tester = EdgeCaseTester()
    success = tester.run_all()
    sys.exit(0 if success else 1)
