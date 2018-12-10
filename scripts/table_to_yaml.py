#!/usr/bin/env python3
import sys
from ruamel import yaml
import jetstream

print(yaml.dump(
    jetstream.utils.load_table(sys.argv[1]),
    default_flow_style=False
))
