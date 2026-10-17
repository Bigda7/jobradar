package main

import (
	"strings"
	"testing"
)

func approvedGraphs() (map[string]module, map[string]module) {
	before := map[string]module{
		"github.com/tianon/gosu":   {Path: "github.com/tianon/gosu", Main: true},
		"github.com/moby/sys/user": {Path: "github.com/moby/sys/user", Version: "v0.1.0"},
		"golang.org/x/sys":         {Path: "golang.org/x/sys", Version: "v0.1.0"},
	}
	after := make(map[string]module)
	for path, entry := range before {
		after[path] = entry
	}
	after["github.com/moby/sys/user"] = module{Path: "github.com/moby/sys/user", Version: "v0.4.1"}
	after["golang.org/x/sys"] = module{Path: "golang.org/x/sys", Version: "v0.49.0"}
	return before, after
}

func TestApprovedGraph(t *testing.T) {
	before, after := approvedGraphs()
	if err := verify(before, after); err != nil {
		t.Fatal(err)
	}
}

func TestUnapprovedGraphs(t *testing.T) {
	for _, change := range []string{"extra", "missing", "version", "baseline", "replace", "main"} {
		t.Run(change, func(t *testing.T) {
			before, after := approvedGraphs()
			entry := after["golang.org/x/sys"]
			switch change {
			case "extra":
				after["unexpected"] = module{Path: "unexpected", Version: "v1.0.0"}
			case "missing":
				delete(after, "golang.org/x/sys")
			case "version":
				entry.Version = "v0.50.0"
				after[entry.Path] = entry
			case "baseline":
				before[entry.Path] = entry
			case "replace":
				entry.Replace = &module{Path: "unexpected"}
				after[entry.Path] = entry
			case "main":
				entry.Main = true
				after[entry.Path] = entry
			}
			if verify(before, after) == nil {
				t.Fatal("unapproved graph accepted")
			}
		})
	}
}

func TestInvalidGraphInput(t *testing.T) {
	for _, input := range []string{`{`, `{}`, `{"Path":"a"}{"Path":"a"}`, `{"Path":"a","Replace":{"Path":"b"}}`} {
		if _, err := readModules(strings.NewReader(input)); err == nil {
			t.Fatalf("accepted %s", input)
		}
	}
}
