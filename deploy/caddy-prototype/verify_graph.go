package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type module struct {
	Path    string
	Version string
	Dir     string
	Replace *module
}

func read(path string) map[string]module {
	file, err := os.Open(path)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	result := map[string]module{}
	decoder := json.NewDecoder(file)
	for {
		var item module
		err = decoder.Decode(&item)
		if err == io.EOF {
			break
		}
		if err != nil {
			panic(err)
		}
		if item.Replace != nil {
			panic("Unexpected module replacement")
		}
		result[item.Path] = item
	}
	return result
}

func main() {
	before, after := read(os.Args[1]), read(os.Args[2])
	expected := map[string]string{
		"golang.org/x/net": "v0.61.0", "golang.org/x/crypto": "v0.58.0",
		"golang.org/x/sys": "v0.49.0", "golang.org/x/term": "v0.47.0",
		"golang.org/x/text": "v0.43.0",
		"golang.org/x/sync": "v0.24.0", "golang.org/x/tools": "v0.51.0",
	}
	if len(before) != len(after) {
		panic("Unexpected module addition/removal")
	}
	changes := map[string][2]string{}
	for path, item := range after {
		old, ok := before[path]
		if !ok {
			panic("Unexpected module addition")
		}
		if old.Version != item.Version {
			if expected[path] != item.Version {
				panic("Unapproved dependency version change: " + path)
			}
			changes[path] = [2]string{old.Version, item.Version}
		}
	}
	for path, version := range expected {
		if after[path].Version != version {
			panic("Required dependency version not resolved")
		}
	}
	encoded, _ := json.Marshal(changes)
	fmt.Println(string(encoded))
	missing := []string{}
	for path, item := range after {
		if item.Version == "" {
			continue
		}
		if item.Dir == "" {
			missing = append(missing, path+"@"+item.Version)
			continue
		}
		matches, err := filepath.Glob(filepath.Join(item.Dir, "*"))
		if err != nil {
			panic(err)
		}
		copied := false
		for _, source := range matches {
			name := strings.ToUpper(filepath.Base(source))
			if !(strings.HasPrefix(name, "LICENSE") || strings.HasPrefix(name, "COPYING") || strings.HasPrefix(name, "NOTICE")) {
				continue
			}
			stat, err := os.Stat(source)
			if err != nil || !stat.Mode().IsRegular() {
				continue
			}
			targetDir := filepath.Join(os.Args[3], filepath.FromSlash(path)+"@"+item.Version)
			if err = os.MkdirAll(targetDir, 0755); err != nil {
				panic(err)
			}
			content, err := os.ReadFile(source)
			if err != nil {
				panic(err)
			}
			if err = os.WriteFile(filepath.Join(targetDir, filepath.Base(source)), content, 0644); err != nil {
				panic(err)
			}
			copied = true
		}
		if !copied {
			missing = append(missing, path+"@"+item.Version)
		}
	}
	encoded, _ = json.Marshal(map[string]any{"modules": len(after), "missing_root_license_files": missing})
	if err := os.WriteFile(filepath.Join(os.Args[3], "inventory.json"), encoded, 0644); err != nil {
		panic(err)
	}
	fmt.Println(string(encoded))
}
