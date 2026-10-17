#!/bin/sh
# Compare optional official W3C corpora against the old and actual packaged library.
set -eu
cd /inputs
echo '96151685cec997e1f9f3387e3626d61e6284d4d6e66e0e440c209286c03e9cc7  xmlts20080827.tar.gz
55e5c08db29946a91ea8e70e8f2418d3fd30d8b6777941dfba7f54726ffd9914  xsts-2002-01-16.tar.gz
09bdf9f81f381ebf9bc158a9472e498e896f7a02eb7461146e9abe1b9493ca17  xsts-2004-01-14.tar.gz' | sha256sum -c -
mkdir -p /tmp/external/xstc/Tests
cd /tmp/external
tar --no-same-owner -xzf /inputs/xmlts20080827.tar.gz
tar --no-same-owner -xzf /inputs/xsts-2002-01-16.tar.gz --strip-components=1 -C xstc/Tests
tar --no-same-owner -xzf /inputs/xsts-2004-01-14.tar.gz -C xstc
for name in MSXMLSchema1-0-20020116.testSet SunXMLSchema1-0-20020116.testSet; do
    cp "xstc/Tests/$name" xstc/Tests/Metadata/
done
cp -a /work/final/test .
for mode in baseline candidate; do
    if test "$mode" = candidate; then
        export LD_LIBRARY_PATH=/candidate
    else
        unset LD_LIBRARY_PATH
    fi
    for suite in runxmlconf runsuite; do
        set +e
        "/work/final/.libs/$suite" > "/output/$suite.$mode.out" 2>&1
        status=$?
        set -e
        printf '%s %s exit_status=%s\n' "$suite" "$mode" "$status" >> /output/status.txt
        test "$status" -eq 0 || test "$status" -eq 1
        cp "$suite.log" "/output/$suite.$mode.details.log"
        grep -E '^(Ran |Total |[0-9]+ errors)' "/output/$suite.$mode.out" \
            > "/output/$suite.$mode.summary"
        echo "$suite $mode (exit_status=$status)"
        cat "/output/$suite.$mode.summary"
    done
done
for suite in runxmlconf runsuite; do
    cmp "/output/$suite.baseline.summary" "/output/$suite.candidate.summary"
    cmp "/output/$suite.baseline.details.log" "/output/$suite.candidate.details.log"
done
echo 'External corpus summaries and detailed logs match the previous library; raw results retained'
