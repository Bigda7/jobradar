package main

import (
	"strings"
	"testing"

	"github.com/moby/sys/user"
)

func TestOversizedPasswdAndGroupLines(t *testing.T) {
	oversized := strings.Repeat("x", 2*1024*1024)
	if _, err := user.ParsePasswd(strings.NewReader("fixture:x:1001:1001:" + oversized + ":/fixture:/bin/sh\n")); err == nil {
		t.Fatal("oversized passwd line accepted")
	}
	if _, err := user.ParseGroup(strings.NewReader("fixture:x:1001:" + oversized + "\n")); err == nil {
		t.Fatal("oversized group line accepted")
	}
}

func TestNormalUserAndSupplementaryGroups(t *testing.T) {
	passwd := strings.NewReader("fixture:x:1001:1002:Fixture:/fixture:/bin/sh\n")
	group := strings.NewReader("primary:x:1002:\nextra:x:1003:fixture\n")
	value, err := user.GetExecUser("fixture", nil, passwd, group)
	if err != nil {
		t.Fatal(err)
	}
	if value.Uid != 1001 || value.Gid != 1002 || value.Home != "/fixture" || len(value.Sgids) != 1 || value.Sgids[0] != 1003 {
		t.Fatalf("unexpected user mapping: %+v", value)
	}
}

func TestInvalidUserAndOutOfRangeID(t *testing.T) {
	for _, spec := range []string{"missing", "9999999999999999999999999999999:1"} {
		if _, err := user.GetExecUser(spec, nil, strings.NewReader("fixture:x:1001:1002:Fixture:/fixture:/bin/sh\n"), strings.NewReader("primary:x:1002:\n")); err == nil {
			t.Fatalf("invalid user specification accepted: %s", spec)
		}
	}
}
