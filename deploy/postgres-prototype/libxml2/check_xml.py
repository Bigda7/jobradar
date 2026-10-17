"""Check the installed XML library and SQL XML/XSLT using isolated synthetic databases."""

import argparse
import importlib.util
import json
import uuid
from pathlib import Path


def load_checker():
    path = Path(__file__).resolve().parents[1] / "check_candidate.py"
    spec = importlib.util.spec_from_file_location("postgres_xml_fixture_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load isolated fixture checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_result(fixture, query: str, expected: str) -> None:
    value = fixture.sql(query).decode().strip()
    if value != expected:
        raise RuntimeError("Unexpected synthetic XML query result")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--candidate-image", required=True)
    args = parser.parse_args()
    checker = load_checker()
    fixture = checker.Fixture(args.candidate_image, args.architecture, "xml", uuid.uuid4().hex[:12])
    passed = []

    def success(name: str) -> None:
        passed.append(name)
        print(json.dumps({"architecture": args.architecture, "passed": name}), flush=True)

    try:
        fixture.start()
        fixture.inspect()
        checker.run(
            [
                "docker",
                "exec",
                fixture.name,
                "/usr/local/share/jobradar-postgres-prototype/libxml2/runtime-regressions",
            ]
        )
        success("installed_library_reader_schema_xinclude_xpath_regexp_dictionary_uri")
        check_result(fixture, "SELECT xpath('/a/b/text()', '<a><b>42</b></a>'::xml)::text;", "{42}")
        success("postgres_xpath")
        check_result(
            fixture,
            "SELECT xml_is_well_formed_document('<a><b/></a>'), "
            "xml_is_well_formed_document('<a><b></a>');",
            "t|f",
        )
        success("valid_and_malformed_sql_xml")
        check_result(
            fixture,
            "SELECT xmlelement(name item, xmlattributes('safe' AS kind), 'a&b')::text;",
            '<item kind="safe">a&amp;b</item>',
        )
        success("sql_xml_serialization_and_escaping")
        check_result(
            fixture,
            "SELECT value FROM XMLTABLE('/a/b' PASSING XMLPARSE(DOCUMENT '<a><b>42</b></a>') "
            "COLUMNS value int PATH 'text()');",
            "42",
        )
        success("sql_xmltable")
        fixture.sql("CREATE EXTENSION xml2;")
        check_result(fixture, "SELECT xpath_string('<a><b>42</b></a>', '/a/b');", "42")
        success("xml2_extension")
        check_result(
            fixture,
            "SELECT xslt_process('<a><b>42</b></a>', "
            '\'<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:template match="/">'
            '<xsl:value-of select="/a/b"/></xsl:template></xsl:stylesheet>\');',
            "42",
        )
        success("libxslt_transformation")
    finally:
        fixture.close()
    print(
        json.dumps({"architecture": args.architecture, "checks": passed, "fixtures_removed": True})
    )


if __name__ == "__main__":
    main()
