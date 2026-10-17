"""Generate strict, reviewable backports from the checksummed release and upstream fixes."""

import difflib
import hashlib
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_SHA512 = (
    "62d4813860124c969f204aaf33b497105dbc32a6c5655f5a86168743660e10987d687d7e5e7ee49fdfdeb8f6ad9"
    "fa4503f81fcce2e4d459094895f02436d1b13"
)
DIRECT = (
    "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2",
    "e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55",
    "a4cba4b5b5a8c42e155ed42d2d2a44955465a2e4",
    "d1686f91dbda141a752200419d35639fd6b38340",
)
UPSTREAM = {
    "3425dece47c8db600f8d7328ae2d7ddfaa0d7b2d": (
        "693c35fbca8588e194fb03cc200d2e6956678cb404c5a1ce6e660bde51fe43e9"
    ),
    "66c52b3ac6c32ab112ec2a3bf41e6c30948be113": (
        "44bdcc1beadf833247a392b3a2f31e184a4181fead83239cc9ec0a518facb676"
    ),
    "6b3a736c0edc74ceec3d82f5252499d7911b3a58": (
        "8a8153323a3fb797f4f8fcaa6fd12dcdb2081aee8c13baee6f88e22bd5fc87bd"
    ),
    "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2": (
        "e7747a70762040dee3ede34f407102717b643b1180d13da9a4ce704a0749ab7b"
    ),
    "8edbbdb09f24d26a2f900141fddc2b9d014f53b0": (
        "d7ac6d515d52b34e44180f49c4089fe7c5889492daa111cfeb4cdcd101ce73e0"
    ),
    "90f293ba74d28b1d570920382e707586f68ebf35": (
        "f521d43d4bdd669009c0986963c33465f33ef7390343491bf751b25d72a0655d"
    ),
    "a4cba4b5b5a8c42e155ed42d2d2a44955465a2e4": (
        "f207c148a8defa5fcf564a05396748b1cf23e9788ae174a750556efdf9261f29"
    ),
    "b63cd517afecb76582dd9488c55e54ceaf50de61": (
        "49b3c2caba6fcba424ced833b26d67efb6c0749d26e7494f4951db61f55f211f"
    ),
    "c2e233fc1b341685fc99621b2768b503f777a72e": (
        "428632c18c3e817394dbe33cc61a2310b63b14994b3252b85faa2ad95438c1b3"
    ),
    "d1686f91dbda141a752200419d35639fd6b38340": (
        "5e51f8dcadfe3294a15d9e8644d61e8d92bd5b9e72c10cf13ef010eccfbfa4df"
    ),
    "e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55": (
        "88cd6d601c99333d4df079e6852b8f8ace2e8c1b6c0e2a237f77b9c4af7c9cb7"
    ),
}


def replace(text: str, old: str, new: str, count: int = 1) -> str:
    if text.count(old) != count:
        raise ValueError(f"Unexpected backport context count: {old!r}")
    return text.replace(old, new)


def uri(text: str) -> str:
    text = replace(
        text,
        "    len = xmlStrlen(str);\n\n    len += 20;",
        "    len = xmlStrlen(str);\n"
        "    if ((len == 0) || (len > INT_MAX - 20))\n"
        "        return(NULL);\n\n    len += 20;",
    )
    return replace(
        text,
        "            if (len > INT_MAX / 2)\n                return(NULL);",
        "            if (len > INT_MAX / 2) {\n"
        "                xmlFree(ret);\n                return(NULL);\n            }",
    )


def xpointer(text: str) -> str:
    return replace(
        text,
        "    len = xmlStrlen(ctxt->cur);\n    len++;",
        "    len = xmlStrlen(ctxt->cur);\n"
        "    if (((len == 0) && (ctxt->cur != NULL) && (*ctxt->cur != 0)) ||\n"
        "        (len == INT_MAX)) {\n"
        "        xmlXPathPErrMemory(ctxt);\n        xmlFree(name);\n        return;\n    }\n"
        "    len++;",
    )


def xmlio(text: str) -> str:
    for buffer in ("conv", "buffer"):
        text = replace(
            text,
            f"            if (out->writecallback)\n\t        nbchars = xmlBufUse(out->{buffer});\n"
            "            else",
            "            if (out->writecallback) {\n"
            f"                size_t size = xmlBufUse(out->{buffer});\n"
            "                if (size >= INT_MAX) {\n"
            "                    out->error = XML_ERR_INTERNAL_ERROR;\n"
            "                    return(-1);\n                }\n"
            "                nbchars = (int) size;\n            } else",
            2,
        )
        text = replace(
            text,
            "\tret = out->writecallback(out->context,\n"
            f"                                 (const char *)xmlBufContent(out->{buffer}),\n"
            f"                                 xmlBufUse(out->{buffer}));",
            f"        size_t size = xmlBufUse(out->{buffer});\n"
            "        if (size >= INT_MAX) {\n"
            "            out->error = XML_ERR_INTERNAL_ERROR;\n"
            "            return(-1);\n        }\n"
            "        ret = out->writecallback(out->context,\n"
            f"                                 (const char *)xmlBufContent(out->{buffer}),\n"
            "                                 (int) size);",
        )
    return text


def xinclude(text: str) -> str:
    text = replace(
        text,
        "    inputStream = xmlLoadExternalEntity((const char*)url, NULL, pctxt);",
        "    xmlCtxtUseOptions(pctxt, ctxt->parseFlags);\n"
        "    inputStream = xmlLoadExternalEntity((const char*)url, NULL, pctxt);",
    )
    text = replace(
        text,
        "                 (pctxt->errNo != XML_IO_UNKNOWN))",
        "                 (pctxt->errNo != XML_IO_UNKNOWN) &&\n"
        "                 (pctxt->errNo != XML_IO_NETWORK_ATTEMPT))",
    )
    text = replace(
        text,
        "    return(xmlXIncludeProcessFlags(doc, 0));",
        "    return(xmlXIncludeProcessFlags(doc, doc ? doc->parseFlags : 0));",
    )
    return replace(
        text,
        "    return(xmlXIncludeProcessTreeFlags(tree, 0));",
        "    return(xmlXIncludeProcessTreeFlags(tree,\n"
        "                    (tree && tree->doc) ? tree->doc->parseFlags : 0));",
    )


def catalog(text: str) -> str:
    text = replace(
        text,
        "xmlExpandCatalog(xmlCatalogPtr catal, const char *filename)",
        "xmlExpandCatalog(xmlCatalogPtr catal, const char *filename, int depth)",
        2,
    )
    text = replace(
        text, "const char *file, int super) {", "const char *file, int super, int depth) {"
    )
    text = replace(
        text,
        "    base = xmlStrdup((const xmlChar *) file);",
        "    if (depth > MAX_CATAL_DEPTH)\n        return(-1);\n"
        "    base = xmlStrdup((const xmlChar *) file);",
    )
    text = replace(
        text,
        "xmlExpandCatalog(catal, (const char *)filename);",
        "xmlExpandCatalog(catal, (const char *)filename, depth);",
    )
    text = replace(
        text,
        "xmlParseSGMLCatalog(catal, content, filename, 1);",
        "xmlParseSGMLCatalog(catal, content, filename, 1, 0);",
    )
    text = replace(
        text,
        "xmlParseSGMLCatalog(catal, content, filename, 0);",
        "xmlParseSGMLCatalog(catal, content, filename, 0, 0);",
        2,
    )
    marker = "xmlExpandCatalog(xmlCatalogPtr catal, const char *filename, int depth)\n{"
    start = text.index(marker)
    head, body = text[:start], text[start:]
    body = replace(
        body,
        "    int ret;\n\n    if ((catal == NULL) || (filename == NULL))",
        "    int ret;\n\n    if (depth > MAX_CATAL_DEPTH)\n        return(-1);\n"
        "    if ((catal == NULL) || (filename == NULL))",
    )
    body = replace(
        body,
        "xmlParseSGMLCatalog(catal, content, filename, 0, 0);",
        "xmlParseSGMLCatalog(catal, content, filename, 0, depth + 1);",
    )
    return replace(
        head + body,
        "xmlExpandCatalog(xmlDefaultCatalog, filename);",
        "xmlExpandCatalog(xmlDefaultCatalog, filename, 0);",
    )


def relaxng(text: str) -> str:
    text = replace(
        text, "#include <string.h>", "#include <errno.h>\n#include <stdlib.h>\n#include <string.h>"
    )
    text = replace(
        text,
        "    xmlRelaxNGIncludePtr *incTab;       /* array of incs */",
        "    xmlRelaxNGIncludePtr *incTab;       /* array of incs */\n"
        "    int incLimit;               /* Maximum include nesting */",
    )
    marker = "static xmlDocPtr xmlRelaxNGCleanupDoc(xmlRelaxNGParserCtxtPtr ctxt,"
    text = replace(
        text,
        marker,
        "/* Preserve the upstream semi-private setter spelling. */\n"
        "int\nxmlRelaxParserSetIncLImit(xmlRelaxNGParserCtxtPtr ctxt, int limit) {\n"
        "    if ((ctxt == NULL) || (limit < 0))\n        return(-1);\n"
        "    ctxt->incLimit = limit;\n    return(0);\n}\n\n" + marker,
    )
    start = text.index("xmlRelaxNGIncludePush(xmlRelaxNGParserCtxtPtr ctxt,")
    end = text.index("static xmlRelaxNGIncludePtr\nxmlRelaxNGIncludePop", start)
    part = text[start:end]
    part = replace(part, "            return (0);", "            return (-1);", 2)
    part = replace(
        part,
        "    if (ctxt->incNr >= ctxt->incMax) {",
        "    if (ctxt->incNr >= ctxt->incLimit) {\n"
        "        xmlRngPErr(ctxt, (xmlNodePtr)value->doc, XML_RNGP_PARSE_ERROR,\n"
        '                   "xmlRelaxNG: inclusion recursion limit reached\\n", NULL, NULL);\n'
        "        return(-1);\n    }\n    if (ctxt->incNr >= ctxt->incMax) {",
    )
    text = text[:start] + part + text[end:]
    text = replace(
        text,
        "    xmlRelaxNGIncludePush(ctxt, ret);",
        "    if (xmlRelaxNGIncludePush(ctxt, ret) < 0)\n        return(NULL);",
    )
    start = text.index("xmlRelaxNGParse(xmlRelaxNGParserCtxtPtr ctxt)\n{")
    head, body = text[:start], text[start:]
    body = replace(
        body,
        "    if (ctxt == NULL)\n        return (NULL);",
        "    if (ctxt == NULL)\n        return (NULL);\n\n"
        "    if (ctxt->incLimit == 0) {\n"
        '        const char *value = getenv("RNG_INCLUDE_LIMIT");\n'
        "        ctxt->incLimit = 1000;\n"
        "        if (value != NULL) {\n"
        "            char *end;\n            unsigned long limit;\n"
        "            errno = 0;\n            limit = strtoul(value, &end, 10);\n"
        "            if ((errno != 0) || (*end != 0) || (limit > INT_MAX)) {\n"
        "                xmlRngPErr(ctxt, NULL, XML_RNGP_PARSE_ERROR,\n"
        '                           "xmlRelaxNGParse: invalid RNG_INCLUDE_LIMIT %s\\n",\n'
        "                           (const xmlChar *)value, NULL);\n"
        "                return(NULL);\n            }\n"
        "            if (limit != 0)\n                ctxt->incLimit = (int) limit;\n"
        "        }\n    }",
    )
    return head + body


def relaxng_header(text: str) -> str:
    return replace(
        text,
        "XMLPUBFUN void\n\t\t    xmlRelaxNGFreeParserCtxt",
        "XMLPUBFUN int\n"
        "                    xmlRelaxParserSetIncLImit"
        "(xmlRelaxNGParserCtxtPtr ctxt, int limit);\n\n"
        "XMLPUBFUN void\n\t\t    xmlRelaxNGFreeParserCtxt",
    )


def main() -> None:
    archive = HERE / "source.tar.xz"
    if hashlib.sha512(archive.read_bytes()).hexdigest() != SOURCE_SHA512:
        raise ValueError("Source archive checksum mismatch")
    for commit, expected in UPSTREAM.items():
        actual = hashlib.sha256((HERE / "upstream" / f"{commit}.patch").read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Upstream patch checksum mismatch: {commit}")
    (HERE / "upstream/SHA256SUMS").write_text(
        "".join(f"{digest}  {commit}.patch\n" for commit, digest in sorted(UPSTREAM.items())),
        encoding="utf-8",
        newline="\n",
    )
    output = HERE / "generated"
    output.mkdir(exist_ok=True)
    files = []
    with tempfile.TemporaryDirectory(
        prefix="libxml2-backport-", dir=HERE.parents[2] / ".deploy"
    ) as temp:
        folder = Path(temp)
        with tarfile.open(archive) as source:
            source.extractall(folder, filter="data")
        root = folder / "libxml2-2.13.9"
        environment = {**os.environ, "GIT_CEILING_DIRECTORIES": str(folder)}

        def apply(path: Path, *options: str) -> None:
            for check in (("--check",), ()):
                subprocess.run(
                    ["git", "apply", *check, *options, str(path)],
                    cwd=root,
                    env=environment,
                    check=True,
                    capture_output=True,
                )

        apply(HERE / "patches/0001-preserve-CVE-2026-6732.patch")
        apply(HERE / "upstream/CVE-2026-6732-test.patch")
        for commit in DIRECT:
            path = HERE / "upstream" / f"{commit}.patch"
            apply(path)
        original = {}
        for name in (
            "uri.c",
            "xpointer.c",
            "xmlIO.c",
            "xinclude.c",
            "catalog.c",
            "relaxng.c",
            "include/libxml/relaxng.h",
            "xmlcatalog.c",
            "valid.c",
        ):
            original[name] = (root / name).read_text(encoding="utf-8")
        for name, transform in (
            ("uri.c", uri),
            ("xpointer.c", xpointer),
            ("xmlIO.c", xmlio),
            ("xinclude.c", xinclude),
            ("catalog.c", catalog),
            ("relaxng.c", relaxng),
            ("include/libxml/relaxng.h", relaxng_header),
        ):
            text = transform(original[name])
            (root / name).write_text(text, encoding="utf-8", newline="\n")
        apply(
            HERE / "upstream/c2e233fc1b341685fc99621b2768b503f777a72e.patch",
            "--include=xmlcatalog.c",
        )
        valid = original["valid.c"]
        start = valid.index("xmlSnprintfElements(char *buf, int size,")
        end = valid.index("xmlValidateElementContent(xmlValidCtxtPtr ctxt,", start)
        valid = (
            valid[:start]
            + valid[start:end].replace(
                "(buf[len - 1] != '.')", "((len == 0) || (buf[len - 1] != '.'))"
            )
            + valid[end:]
        )
        (root / "valid.c").write_text(valid, encoding="utf-8", newline="\n")
        patch = "Subject: [PATCH] Backport security fixes to libxml2 2.13.9\n\n"
        for name, before in original.items():
            after = (root / name).read_text(encoding="utf-8")
            patch += "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{name}",
                    tofile=f"b/{name}",
                )
            )
        target = output / "0002-security-backports.patch"
        target.write_text(patch, encoding="utf-8", newline="\n")
        files.append(target)
    sums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files
    )
    (output / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    print(sums, end="")


if __name__ == "__main__":
    main()
