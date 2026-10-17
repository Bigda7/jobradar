package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestDependencyGuard(t *testing.T) {
	versions := map[string]string{"golang.org/x/net": "v0.61.0", "golang.org/x/crypto": "v0.58.0", "golang.org/x/sys": "v0.49.0", "golang.org/x/term": "v0.47.0", "golang.org/x/text": "v0.43.0", "golang.org/x/sync": "v0.24.0", "golang.org/x/tools": "v0.51.0"}
	for _, scenario := range []string{"approved", "unchanged_other", "unexpected_upgrade", "added", "removed", "replacement", "missing_required"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			before, after := []module{}, []module{}
			for path, version := range versions {
				before = append(before, module{Path: path, Version: "v0.1.0"})
				after = append(after, module{Path: path, Version: version})
			}
			switch scenario {
			case "unchanged_other":
				before = append(before, module{Path: "fixture/unchanged", Version: "v1.0.0"})
				after = append(after, module{Path: "fixture/unchanged", Version: "v1.0.0"})
			case "unexpected_upgrade":
				before = append(before, module{Path: "fixture/unapproved", Version: "v1.0.0"})
				after = append(after, module{Path: "fixture/unapproved", Version: "v2.0.0"})
			case "added":
				after = append(after, module{Path: "fixture/added", Version: "v1.0.0"})
			case "removed":
				after = after[1:]
			case "replacement":
				after[0].Replace = &module{Path: "fixture/replacement"}
			case "missing_required":
				after[0].Version = "v0.1.0"
			}
			for name, items := range map[string][]module{"before.json": before, "after.json": after} {
				file, err := os.Create(filepath.Join(root, name))
				if err != nil {
					t.Fatal(err)
				}
				for _, item := range items {
					if err := json.NewEncoder(file).Encode(item); err != nil {
						t.Fatal(err)
					}
				}
				file.Close()
			}
			oldArgs := os.Args
			defer func() { os.Args = oldArgs }()
			os.Args = []string{"fixture", filepath.Join(root, "before.json"), filepath.Join(root, "after.json"), root}
			panicked := false
			func() {
				defer func() {
					if recover() != nil {
						panicked = true
					}
				}()
				main()
			}()
			wantPanic := scenario != "approved" && scenario != "unchanged_other"
			if panicked != wantPanic {
				t.Fatalf("guard rejection=%v, expected=%v", panicked, wantPanic)
			}
		})
	}
}
