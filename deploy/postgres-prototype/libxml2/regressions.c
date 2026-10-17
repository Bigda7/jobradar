/* Synthetic local regressions; no live services or large allocations. */
#define _GNU_SOURCE
#define XML_DEPRECATED
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <libxml/parser.h>
#include <libxml/parserInternals.h>
#include <libxml/xmlmemory.h>
#include <libxml/xmlreader.h>
#include <libxml/xmlschemas.h>
#include <libxml/xmlregexp.h>
#include <libxml/relaxng.h>
#include <libxml/xinclude.h>
#include <libxml/xpointer.h>
#include <libxml/xpathInternals.h>
#include <libxml/uri.h>
#include <libxml/catalog.h>
#include <libxml/encoding.h>

static void marker(const char *name) {
    printf("PROBE:%s\n", name);
    fflush(stdout);
}

#if defined(TEST_REGEXP)
static xmlChar *failedStrdup(const xmlChar *value) {
    (void)value;
    return NULL;
}
#define xmlStrdup failedStrdup
#include "xmlregexp.c"
#undef xmlStrdup
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    marker(argv[1]);
    if (!strcmp(argv[1], "alloc")) {
        assert(xmlRegNewParserCtxt(BAD_CAST "x") == NULL);
    } else {
        size_t page = (size_t)sysconf(_SC_PAGESIZE);
        char *memory = mmap(NULL, page * 2, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        xmlRegParserCtxt context;
        xmlRegParserCtxtPtr ctxt = &context;
        assert(memory != MAP_FAILED);
        assert(mprotect(memory + page, page, PROT_NONE) == 0);
        memset(ctxt, 0, sizeof(*ctxt));
        ctxt->string = ctxt->cur = (xmlChar *)(memory + page - 1);
        ctxt->cur[0] = 'x';
#ifdef HAS_REGEXP_LENGTH
        ctxt->len = 1;
#endif
        assert(NXT(1) == 0);
        assert(munmap(memory, page * 2) == 0);
    }
    puts("PASS");
    return 0;
}
#elif defined(TEST_VALID)
#include "valid.c"
int main(int argc, char **argv) {
    xmlNodePtr node = xmlNewNode(NULL, BAD_CAST "test");
    assert(argc == 2 && node != NULL);
    marker(argv[1]);
    if (!strcmp(argv[1], "tiny")) {
        struct { char buffer[1]; unsigned char canary[64]; } value;
        memset(&value, 0x5a, sizeof(value));
        value.buffer[0] = 0;
        xmlSnprintfElements(value.buffer, 1, node, 1);
        if (value.buffer[0] != 0 || value.canary[0] != 0x5a) {
            puts("BOUNDS_VIOLATION");
            return 10;
        }
    } else {
        size_t page = (size_t)sysconf(_SC_PAGESIZE);
        char *memory = mmap(NULL, page * 2, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        assert(memory != MAP_FAILED);
        assert(mprotect(memory, page, PROT_NONE) == 0);
        memory[page] = 0;
        xmlSnprintfElements(memory + page, 8, node, 1);
        assert(munmap(memory, page * 2) == 0);
    }
    xmlFreeNode(node);
    puts("PASS");
    return 0;
}
#elif defined(TEST_DICT)
static size_t allocation_size;
static void *denyAllocation(size_t size) {
    allocation_size = size;
    return NULL;
}
static xmlMallocFunc deniedMalloc = denyAllocation;
#define xmlMalloc deniedMalloc
#include "dict.c"
#undef xmlMalloc
int main(void) {
    xmlDict value;
    memset(&value, 0, sizeof(value));
    marker("dictionary-large-qname");
    assert(xmlDictAddQString(&value, BAD_CAST "p", 0x40000000U, BAD_CAST "n", 1) == NULL);
    if (allocation_size < (size_t)0x40000000U + 3) {
        puts("UNDERSIZED_ALLOCATION");
        return 11;
    }
    puts("PASS");
    return 0;
}
#elif defined(TEST_URI) || defined(TEST_XPOINTER)
static int forced_length;
static int allocations;
static int fakeLength(const xmlChar *value) {
    (void)value;
    return forced_length;
}
static void *recordAllocation(size_t size) {
    (void)size;
    allocations++;
    return NULL;
}
static xmlMallocFunc recordedAtomicMalloc = recordAllocation;
#define xmlStrlen fakeLength
#define xmlMallocAtomic recordedAtomicMalloc
#ifdef TEST_URI
#include "uri.c"
#else
#include "xpointer.c"
#endif
#undef xmlStrlen
#undef xmlMallocAtomic
int main(int argc, char **argv) {
    assert(argc == 2);
    forced_length = atoi(argv[1]);
    marker("unrepresentable-length");
#ifdef TEST_URI
    assert(xmlURIEscapeStr(BAD_CAST "x", NULL) == NULL);
#else
    xmlDocPtr doc = xmlReadMemory("<a/>", 4, NULL, NULL, XML_PARSE_NONET);
    xmlXPathContextPtr xpath = xmlXPtrNewContext(doc, NULL, NULL);
    xmlXPathParserContextPtr parser = xmlXPathNewParserContext(BAD_CAST "(x)", xpath);
    xmlXPtrEvalXPtrPart(parser, xmlStrdup(BAD_CAST "xpointer"));
    xmlXPathFreeParserContext(parser);
    xmlXPathFreeContext(xpath);
    xmlFreeDoc(doc);
#endif
    if (allocations != 0) {
        puts("ALLOCATION_BEFORE_LENGTH_REJECTION");
        return 12;
    }
    puts("PASS");
    return 0;
}
#elif defined(TEST_IO)
static size_t forced_size;
static size_t testBufUse(xmlBufPtr buffer);
#define xmlBufUse testBufUse
#include "xmlIO.c"
#undef xmlBufUse
extern size_t xmlBufUse(xmlBufPtr buffer);
static size_t testBufUse(xmlBufPtr buffer) {
    return forced_size ? forced_size : xmlBufUse(buffer);
}
static int callback_calls;
static int outputCallback(void *context, const char *buffer, int length) {
    (void)context;
    (void)buffer;
    (void)length;
    callback_calls++;
    return 0;
}
int main(int argc, char **argv) {
    int encoded, flush, ret;
    xmlOutputBufferPtr output;
    xmlCharEncodingHandlerPtr encoding = NULL;
    assert(argc == 3);
    encoded = atoi(argv[1]);
    flush = atoi(argv[2]);
    if (encoded) encoding = xmlFindCharEncodingHandler("UTF-16LE");
    assert(!encoded || encoding != NULL);
    output = xmlOutputBufferCreateIO(outputCallback, NULL, NULL, encoding);
    assert(output != NULL);
    forced_size = (size_t)INT_MAX + 1;
    marker("output-callback-length");
    ret = flush ? xmlOutputBufferFlush(output) : xmlOutputBufferWrite(output, 1, "x");
    forced_size = 0;
    if (ret != -1 || callback_calls != 0 || output->error != XML_ERR_INTERNAL_ERROR) {
        puts("INVALID_CALLBACK_LENGTH_NOT_REJECTED");
        return 13;
    }
    xmlOutputBufferClose(output);
    puts("PASS");
    return 0;
}
#else
static int network_attempts;
static xmlExternalEntityLoader originalLoader;
static xmlParserInputPtr controlledLoader(const char *url, const char *id,
                                          xmlParserCtxtPtr ctxt) {
    if (url != NULL && !strncmp(url, "http://", 7)) {
        if (!(ctxt->options & XML_PARSE_NONET)) network_attempts++;
        return NULL;
    }
    return originalLoader(url, id, ctxt);
}
static int readerSchema(void) {
    const char *xsd = "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'>"
                      "<xs:element name='e' type='xs:integer'/></xs:schema>";
    const char *xml = "<!DOCTYPE e [<!ENTITY n 'not-an-int'>]><e>&n;</e>";
    xmlSchemaParserCtxtPtr spc = xmlSchemaNewMemParserCtxt(xsd, (int)strlen(xsd));
    xmlSchemaPtr schema = xmlSchemaParse(spc);
    xmlTextReaderPtr reader;
    int ret;
    assert(schema != NULL);
    xmlSchemaFreeParserCtxt(spc);
    reader = xmlReaderForMemory(xml, (int)strlen(xml), "local.xml", NULL,
                                XML_PARSE_NOENT | XML_PARSE_DTDLOAD | XML_PARSE_NONET);
    assert(reader != NULL && xmlTextReaderSetSchema(reader, schema) == 0);
    while ((ret = xmlTextReaderRead(reader)) == 1) {}
    assert(ret == 0 && xmlTextReaderIsValid(reader) == 0);
    xmlFreeTextReader(reader);
    xmlSchemaFree(schema);
    return 0;
}
static int includeFlags(void) {
    int tree, text;
    originalLoader = xmlGetExternalEntityLoader();
    xmlSetExternalEntityLoader(controlledLoader);
    for (tree = 0; tree < 2; tree++) {
        for (text = 0; text < 2; text++) {
            char content[400];
            xmlDocPtr doc;
            snprintf(content, sizeof(content),
                "<d xmlns:xi='http://www.w3.org/2001/XInclude'>"
                "<xi:include href='http://jobradar.invalid/input'%s>"
                "<xi:fallback>blocked</xi:fallback></xi:include></d>",
                text ? " parse='text'" : "");
            doc = xmlReadMemory(content, (int)strlen(content), NULL, NULL, XML_PARSE_NONET);
            assert(doc != NULL);
            if (tree) xmlXIncludeProcessTree(xmlDocGetRootElement(doc));
            else xmlXIncludeProcess(doc);
            xmlFreeDoc(doc);
        }
    }
    xmlSetExternalEntityLoader(originalLoader);
    if (network_attempts) {
        puts("PARSE_OPTIONS_NOT_PROPAGATED");
        return 14;
    }
    return 0;
}
int main(int argc, char **argv) {
    xmlDocPtr doc;
    xmlXPathContextPtr xpath;
    xmlXPathObjectPtr result;
    xmlRegexpPtr regexp;
    xmlDictPtr dict;
    const xmlChar *name;
    xmlChar *uri;
    if (argc == 2) {
        marker(argv[1]);
        if (!strcmp(argv[1], "schema")) return readerSchema();
        if (!strcmp(argv[1], "xinclude")) return includeFlags();
        if (!strcmp(argv[1], "relaxng")) {
            xmlRelaxNGParserCtxtPtr ctxt = xmlRelaxNGNewParserCtxt("/work/rng/0.rng");
            xmlRelaxNGPtr schema = xmlRelaxNGParse(ctxt);
            if (schema != NULL) { xmlRelaxNGFree(schema); return 15; }
            xmlRelaxNGFreeParserCtxt(ctxt);
            return 0;
        }
        if (!strcmp(argv[1], "catalog")) {
            xmlCatalogPtr catalog = xmlLoadACatalog("/work/catalog/recursive.sgml");
            if (catalog != NULL) xmlFreeCatalog(catalog);
            return 0;
        }
        return 2;
    }
    assert(readerSchema() == 0);
    assert(includeFlags() == 0);
    doc = xmlReadMemory("<a><b>42</b></a>", 16, NULL, NULL, XML_PARSE_NONET);
    assert(doc != NULL);
    xpath = xmlXPathNewContext(doc);
    result = xmlXPathEvalExpression(BAD_CAST "string(/a/b)", xpath);
    assert(result != NULL && xmlStrEqual(result->stringval, BAD_CAST "42"));
    xmlXPathFreeObject(result);
    xmlXPathFreeContext(xpath);
    xmlFreeDoc(doc);
    regexp = xmlRegexpCompile(BAD_CAST "[a-z]+");
    assert(regexp != NULL && xmlRegexpExec(regexp, BAD_CAST "abc") == 1);
    xmlRegFreeRegexp(regexp);
    dict = xmlDictCreate();
    name = xmlDictQLookup(dict, BAD_CAST "p", BAD_CAST "name");
    assert(name != NULL && xmlStrEqual(name, BAD_CAST "p:name"));
    xmlDictFree(dict);
    uri = xmlURIEscapeStr(BAD_CAST "a b", NULL);
    assert(uri != NULL && xmlStrEqual(uri, BAD_CAST "a%20b"));
    xmlFree(uri);
    xmlCleanupParser();
    puts("PASS: reader/schema, XInclude flags, XPath, regexp, dictionary, URI");
    return 0;
}
#endif
