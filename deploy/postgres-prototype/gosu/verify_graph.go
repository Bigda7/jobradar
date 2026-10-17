package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

type module struct {
	Path    string
	Version string
	Main    bool
	Replace *module
}

func readModules(reader io.Reader) (map[string]module, error) {
	decoder := json.NewDecoder(reader)
	modules := make(map[string]module)
	for {
		var entry module
		if err := decoder.Decode(&entry); err == io.EOF {
			return modules, nil
		} else if err != nil {
			return nil, err
		}
		if entry.Path == "" || entry.Replace != nil {
			return nil, fmt.Errorf("missing module path or unexpected replacement")
		}
		if _, exists := modules[entry.Path]; exists {
			return nil, fmt.Errorf("duplicate module: %s", entry.Path)
		}
		modules[entry.Path] = entry
	}
}

func verify(before, after map[string]module) error {
	expected := map[string][2]string{
		"github.com/tianon/gosu":   {"", ""},
		"github.com/moby/sys/user": {"v0.1.0", "v0.4.1"},
		"golang.org/x/sys":         {"v0.1.0", "v0.49.0"},
	}
	if len(before) != len(expected) || len(after) != len(expected) {
		return fmt.Errorf("module count changed or unexpected baseline")
	}
	for path, versions := range expected {
		old, oldOK := before[path]
		updated, newOK := after[path]
		main := path == "github.com/tianon/gosu"
		if !oldOK || !newOK || old.Version != versions[0] || updated.Version != versions[1] || old.Main != main || updated.Main != main || old.Replace != nil || updated.Replace != nil {
			return fmt.Errorf("unapproved module change: %s", path)
		}
	}
	return nil
}

func main() {
	if len(os.Args) != 3 {
		panic("expected original and updated module graphs")
	}
	graphs := make([]map[string]module, 0, 2)
	for _, path := range os.Args[1:] {
		file, err := os.Open(path)
		if err != nil {
			panic(err)
		}
		entries, err := readModules(file)
		file.Close()
		if err != nil {
			panic(err)
		}
		graphs = append(graphs, entries)
	}
	if err := verify(graphs[0], graphs[1]); err != nil {
		panic(err)
	}
	fmt.Println("Verified exactly two approved dependency updates and no new modules")
}
