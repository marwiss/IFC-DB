# IFC–LPG round-tripping

Lossless round-tripping between IFC STEP files and labelled property graphs in
Neo4j and Memgraph.

An IFC model is imported into a graph database with one node per instance, so
that it can be queried with Cypher next to other data. The file can then be
written back from the graph: the same instances with the same STEP ids, and the
same values in the same order. A property or a relationship changed in the
graph shows up in the written file, and the written file is validated against
the IFC standard.

> **Status.** The code came here in November 2026 from
> [Kontroll](https://github.com/marwiss/Kontroll), the OpenCDE server where it
> was developed and tested, and Kontroll still runs a copy of its own. The
> package is not on PyPI yet.

## Contents

- [Background](#background)
- [How a model is imported](#how-a-model-is-imported)
- [The graph](#the-graph)
- [How STEP ids are kept](#how-step-ids-are-kept)
- [How geometry is stored](#how-geometry-is-stored)
- [How a model is exported](#how-a-model-is-exported)
- [When two files hold the same model](#when-two-files-hold-the-same-model)
- [Validation](#validation)
- [Pointing at an instance: STEP id, or GlobalId and path](#pointing-at-an-instance-step-id-or-globalid-and-path)
- [Neo4j and Memgraph](#neo4j-and-memgraph)
- [Measurements](#measurements)
- [Using it](#using-it)
- [Licence](#licence)
- [References](#references)

## Background

In 2023 this repository held an implementation of the IFC-graph method of Zhu,
Wu and Lei, rewritten for the official Neo4j driver for Python after py2neo was
discontinued. The method maps an IFC model onto a labelled property graph: one
node per instance, the class hierarchy as labels, literal attributes as
properties, and attributes that point at other instances, inverse attributes
included, as relationships named after the attribute. Such a graph answers
questions that are awkward to ask of a file, such as which walls carry a given
property, and lets building data sit in one database with issues, documents and
other data.

Two limits of the method matter as soon as the graph is to be more than a copy
for reading. The article's graph loses the order of lists, since a relationship
does not say where in a list its target stood, and it turns all geometry into
nodes, which the article names as a cost in size. Neither lets the file be
written back.

In 2026 the conversion was written anew in Kontroll, an OpenCDE server, where it
joins the components of BCF viewpoints to the building elements they point at.
There it gained what a round trip needs: list positions, typed values, the
header, geometry kept as text, a STEP reader and writer of its own, validation,
and a comparison that says whether two files hold the same model. This
repository now carries that work under a new name, IFC-LPG: IFC as a labelled
property graph.

## How a model is imported

1. **The file is read by the project's own STEP reader** (ISO 10303-21).
   IfcOpenShell supplies the schema's tables and nothing else. Its parser
   changes some values on reading, such as `.NULLENUM.` to `$` and `($,$)` to
   `()`, which files from Tekla and Archicad contain, so a round trip through it
   could not give the same file back. The reader refuses what it cannot read: a
   truncated file is an error, not a shorter model. Its patterns cannot
   backtrack, and nesting is limited.
2. **The conversion runs in a separate process**, with a time limit, a memory
   limit on POSIX and a cap on the number of instances, so that a crash or a
   hang ends that process and not the program that started it.
3. **Every instance becomes a record**, and each value is read back from the form
   it is given as it is written. What does not come back the same goes into
   tagged JSON instead, so the values that need it are found by trying, not from
   a list that could be incomplete: a string where the schema has an
   enumeration, an INTEGER no database holds, raw bytes, a REAL beyond a
   double's range, a reference to nothing.
4. **The records are written in batches** of `UNWIND … CREATE`, with
   parameters only. Labels and relationship types from the schema are checked
   as identifiers before they reach a query. The article merged every node and
   relationship, which searches the graph for duplicates and slows down as the
   graph grows.

Several models share one database, told apart by `model_id`.

## The graph

Every instance becomes an `IfcEntity` node with its class and all its
supertypes as labels:

```text
(:IfcEntity:IfcWallStandardCase:IfcWall:IfcBuildingElement:IfcElement:IfcProduct:IfcObject:IfcObjectDefinition:IfcRoot)
```

| Property | Meaning |
|---|---|
| `id` | `<model id>:<STEP id>`, unique |
| `model_id`, `step_id` | the model, and the number of the instance in the file, as in `#123` |
| `ifc_class` | the concrete class |
| `GlobalId`, `Name`, `Description`, `Tag`, … | every literal attribute, by its IFC name |

An attribute that points at another instance becomes a relationship named after
it, in upper case with underscores at the word boundaries. An inverse attribute
becomes a relationship too, so both directions read naturally:

```cypher
// The GTIN of every wall, through its property sets.
MATCH (w:IfcWall)-[:IS_DEFINED_BY]->(:IfcRelDefinesByProperties)-[:RELATING_PROPERTY_DEFINITION]
      ->(:IfcPropertySet {Name: 'Reuse'})-[:HAS_PROPERTIES]->(p:IfcPropertySingleValue {Name: 'GTIN'})
RETURN w.GlobalId, w.Name, p.NominalValue
```

How the values of the file are kept:

| In the file | In the graph |
|---|---|
| A reference | A relationship with `position`: `0` for a single reference, the index in a list, `[row, column]` in a list of lists. Inverse relationships have none |
| A reference to an instance in a block | Its STEP id, as in `Representation__step: 1234` |
| A typed value, `IFCLABEL('3')` | `NominalValue: "3"`, and `NominalValue__type: "IFCLABEL"` beside it |
| A list | A list property |
| A list of lists of numbers | JSON text, since Neo4j has no nested lists |
| What has no plain form, such as a list of typed values | Tagged JSON, with `$json` as its type |
| The header | `ifc_header` on the `IfcModel` node |
| An instance in a block | STEP text in a `StepBlock` node |

An `IfcOwnerHistory` is linked from its `IfcProject` only. Linked from every
instance that shares it, it would become the centre of the graph.

## How STEP ids are kept

Every node keeps the number its instance has in the file, in `step_id`, and its
`id` is `<model id>:<STEP id>`. A reference to an instance that is no node,
because it is in a block, keeps that instance's number in `<attribute>__step`.
The writer gives every instance its number back.

So the written file has the same instances with the same ids, in the same order
and with the same values. Native IFC tools that merge by id, such as
[ifcmerge](https://github.com/brunopostle/ifcmerge), depend on that, and a change
can name the instance it changes by its STEP id alone, in the graph as in the
file.

## How geometry is stored

Geometry is most of a model and seldom what a query asks for. So representation
items, representations, profiles, placements and styles, and owner histories
other than the project's, are no nodes by default. They are written out as STEP,
in order of id, and compressed into text on `StepBlock` nodes that hang off the
`IfcModel`: at most 256 KiB of STEP per block, deflated and in base64, since a
string is the one thing both databases store alike.

The blocks are drawn by class, not by product, because 17 to 94 per cent of the
geometry in the sample models belongs to more than one product, and some of it to
none. That is also why geometry cannot be properties of the products it shapes.

What is in a block is in the graph, but out of Cypher's reach, and it cannot be
changed there. A product that refers to it keeps the STEP id of what it refers
to, as a number a query can read. Every instance can be a node instead, at
several times the size and the import time. Typed queries took about as long
either way in the [measurements](#measurements), so the reason to keep geometry
in blocks is what it costs to store and import, not what it costs to ask.

## How a model is exported

The file is written from what the graph holds, not copied: the nodes in order of
STEP id with their properties and relationships, the instances in the blocks, and
the header from the `IfcModel`. A property or a relationship changed in the
graph therefore shows up in the file.

The writer reads only what the schema says is an attribute, so inverse
relationships and whatever else has been added to the graph stay behind. A file
is written a line at a time from the graph's pages, in order of id, so its size
does not decide the memory it takes. An export stops if the blocks do not hold
the number of instances the conversion put in them, since a file without them
could not be told from a right one.

## When two files hold the same model

A comparison says whether two files hold the same model: there is a one-to-one
pairing of their instances in which paired instances have the same class and the
same values, their references lead to paired instances, every LIST and ARRAY has
its order, and every SET and BAG has the same members. All instances count, not
only those reached from an `IfcRoot`, since a map conversion is reached from
none.

There is no tolerance. A REAL is compared by its bits, since a double survives
text, JSON and Bolt exactly when it is written in its shortest form. *Identical*
asks for the same ids, the same order in SETs and the same header too.

The comparison reads the files with the project's own reader, and checks a
pairing it has built against the files alone, so a defect can give a wrong "not
the same" but not a wrong "the same". It can also build every product's mesh
with IfcOpenShell and compare the meshes bit for bit, and it names the
georeference in its report.

## Validation

A graph database has no schema that could refuse a wrong value, and the rules of
IFC are written in EXPRESS, which no graph database holds. So the file is
validated, not the graph:

- **The IFC schema**, with IfcOpenShell's validator and the rules of the EXPRESS
  schema: the type and number of attributes, enumerations, the cardinality of
  inverse attributes, the form and uniqueness of GlobalIds, and WHERE rules and
  global rules. Files in use often break a rule already, so after a change what
  counts is an issue the original file does not have.
- **buildingSMART's
  [ifc-gherkin-rules](https://github.com/buildingSMART/ifc-gherkin-rules)**, the
  normative rules and industry practice of buildingSMART's validation service.
  They run as the rule set's own tests run them, in an environment of their own
  with IfcOpenShell 0.8.6, and without the rule set's Django parts, which only
  write results to the validation service's database. On the rule set's 722
  test files this gives the expected verdict for every one.

## Pointing at an instance: STEP id, or GlobalId and path

A proposed change to a model, such as a BIM snippet in a BCF topic, has to say
which instance it changes. Either of two keys does that on its own, against the
file and against the graph.

- **The STEP id** always works, since every node keeps it.
- **A GlobalId and a path**, close to how IFC5 addresses its data: from the
  nearest rooted instance, through attribute names and list positions.

The second was tried on 28 sample files of up to 224,496 instances:

- Paths forward from a GlobalId reached 87 to 99.5 per cent of the instances,
  and every path led back to the right instance. One small test file reached 22
  per cent, which is not yet explained.
- With steps backwards through inverse attributes, as styles and the
  georeference need, 98 to 100 per cent. Such a step was ambiguous for under 0.5
  per cent of the instances in most files, and for 5.5 per cent at most.
- The 0 to 1.9 per cent that no path reaches are unused points and placements,
  which only their STEP id names.
- In the graph, 1,576 sampled paths in four models were followed step by step
  with parameterised Cypher, and every one ended at the right `step_id`.

How a path is written, and what a change to an instance that several others share
should mean, is still to be decided.

## Neo4j and Memgraph

The Cypher is parameterised and portable: no APOC, and no `elementId()` or
`id()` as keys. A model comes back identical from both databases. The tests run
on Neo4j 5.26 and Memgraph 3.14.

## Measurements

Without a database, all 26 whole files among the samples came back identical, up
to 224,496 instances and 19 MB, from Revit, Archicad, Tekla, SketchUp, SierraSoft
and IfcOpenShell. IfcOpenShell's own written form of original and copy was equal
on every one, and the generated meshes were equal bit for bit, up to 4,134
products in one model. Six truncated files, and one in a schema IfcOpenShell does
not have, were refused.

Five real models went through a fresh database of each kind, in three forms, and
all came back identical. *Ordinary* is the form without geometry, which cannot be
written back; *blocks* keeps geometry as text; *nodes* makes every instance a
node.

| Model | File | Instances | Neo4j store, MB: ordinary / blocks / nodes | Memgraph memory, MB: ordinary / blocks / nodes | Import on Neo4j, s: ordinary / blocks / nodes |
|---|---|---|---|---|---|
| SketchUp, tessellated | 5.6 MB | 23,528 | 2.3 / 3.9 / 19.6 | 13 / 17 / 48 | 3 / 3 / 12 |
| Archicad | 2.8 MB | 49,664 | 6.6 / 8.3 / 21.0 | 26 / 27 / 55 | 13 / 13 / 32 |
| Archicad | 6.0 MB | 110,176 | 11.3 / 14.6 / 42.8 | 37 / 39 / 95 | 20 / 15 / 53 |
| SierraSoft, road | 14.6 MB | 127,491 | 4.8 / 10.6 / 54.7 | 21 / 27 / 130 | 9 / 7 / 38 |
| Tekla | 19.2 MB | 224,496 | 55.8 / 108.9 / 137.2 | 144 / 149 / 264 | 58 / 32 / 94 |

Queries from a storey to its elements and through property sets took 7 to 25 and
11 to 90 ms on Neo4j in all three forms, with no pattern among them. This is one
run on one machine with other work going on, on models of moderate size; a model
of millions of instances needs measuring first.

Four pairs of original and written copy, opened in Bonsai 0.8.3 and in FreeCAD
1.0.1 through NativeIFC, gave the same objects, products and meshes for both.
FreeCAD keeps how a REAL is spelled, so one line of one file read `1.0E-5` in the
original and `1.E-05` in the copy, the same number.

## Using it

IFC–LPG needs Python 3.11 or later. It is installed from this repository, with
the `neo4j` extra for the official driver:

```sh
pip install "ifc-lpg[neo4j] @ git+https://github.com/marwiss/IFC-LPG@v1.0.0"
```

A file goes into the graph as a model, and comes back out as a file. With Neo4j:

```python
import asyncio
import os
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ifc_lpg.files import export_file, import_file
from ifc_lpg.graph import Neo4jGraph, ensure_schema


async def main() -> None:
    auth = ("neo4j", os.environ["NEO4J_PASSWORD"])
    async with AsyncGraphDatabase.driver("bolt://localhost:7687", auth=auth) as driver:
        graph = Neo4jGraph(driver)
        await ensure_schema(graph, "neo4j")
        model = await import_file(graph, Path("house.ifc"), "house")
        print(model["entities"], "instances")
        await export_file(graph, "house", Path("house-again.ifc"))


asyncio.run(main())
```

The same with Memgraph, which the official driver reaches over Bolt too. A
Memgraph without users takes empty credentials, and one with users takes a user
name and password as Neo4j does. The schema is made in Memgraph's words:

```python
import asyncio
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ifc_lpg.files import export_file, import_file
from ifc_lpg.graph import Neo4jGraph, ensure_schema


async def main() -> None:
    async with AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("", "")) as driver:
        graph = Neo4jGraph(driver)
        await ensure_schema(graph, "memgraph")
        model = await import_file(graph, Path("house.ifc"), "house")
        print(model["entities"], "instances")
        await export_file(graph, "house", Path("house-again.ifc"))


asyncio.run(main())
```

`Neo4jGraph` is named after the driver, not the database. `ensure_schema` makes
the constraints and indexes the queries rely on, and leaves those that exist.

`import_file` refuses a model id that is taken. While it loads, the model's
`IfcModel` node is `running`. Afterwards it is `done`, with its counts, or
`failed`, with the error and nothing else of the model left; `delete_model` in
`ifc_lpg.importer` takes a model out of the graph. Geometry goes into blocks by
default. `include_geometry=True` makes every instance a node, and
`lossless=False` leaves the geometry out, so that the model cannot be written
back: the three forms of the [measurements](#measurements). A conversion and a
writing each run in a process of their own, within the limits of
`ifc_lpg.files.Limits`: 900 s, 4,096 MB of memory on POSIX and 5 million
instances, unless others are given.

A program with its own record of a model, as Kontroll ties one to a project and
a document version, uses the parts instead: `convert_file` and `write_file` in
`ifc_lpg.files`, `import_records` in `ifc_lpg.importer` and `export_records` in
`ifc_lpg.exporter`. Kontroll's
[IFC-LPG page](https://github.com/marwiss/Kontroll/blob/main/docs/ifc-lpg.md)
describes how a server uses them.

Two tools work on files alone, without a database. Each answers in JSON and exits
with 0 when all is well:

```sh
python -m ifc_lpg.equivalence first.ifc second.ifc          # do the two files hold the same model?
python -m ifc_lpg.validation model.ifc                      # issues against the IFC schema
python -m ifc_lpg.validation copy.ifc --against model.ifc   # only those the original does not have
```

buildingSMART's rules need an environment of their own, with IfcOpenShell 0.8.6.
The installer makes one in an empty folder on Linux or Windows, from downloads it
checks against pinned digests:

```sh
python -m ifc_lpg.gherkin_install rules          # the rules and their IfcOpenShell, in the folder rules
python -m ifc_lpg.gherkin model.ifc --env rules  # their verdict on a file, in JSON
```

The decisions in [docs/adr](docs/adr) record why it works as it does:
[0009](docs/adr/0009-what-makes-two-ifc-files-the-same-model.md) on what makes
two files the same model,
[0010](docs/adr/0010-a-lossless-graph-reads-and-writes-step-itself.md) on reading
and writing STEP itself,
[0011](docs/adr/0011-a-file-written-from-the-graph-is-validated.md) on validating
the written file, and
[0012](docs/adr/0012-buildingsmarts-rules-run-in-an-environment-of-their-own.md)
on buildingSMART's rules.

## Licence

IFC–LPG is free software under the GNU Affero General Public License, version 3
or later, in [LICENSE](LICENSE). It is the licence of Kontroll, where the code
was written.

## References

Junxiang Zhu, Peng Wu and Xiang Lei: "IFC-graph for facilitating building
information access and query". *Automation in Construction* 148 (2023), 104778.
[doi:10.1016/j.autcon.2023.104778](https://doi.org/10.1016/j.autcon.2023.104778),
open access under CC BY 4.0.
