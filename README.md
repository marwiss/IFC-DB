# IFC–DB

IFC–DB puts a building model into a graph database and gets it back out again,
without losing anything. An IFC file goes in, and the same file comes out. A change
made in the database shows up in the file.

It is a Python library for Neo4j and Memgraph, with portable Cypher. It reads
and writes IFC files in STEP (ISO 10303-21) itself, keeps every instance's STEP
id, and can check the file it writes against the IFC standard, buildingSMART's
rules and IDS.

> **Status.** Version 1.0.0. The package is not on PyPI yet; it is installed from
> this repository. [Dataregium](https://github.com/marwiss/Dataregium), the
> OpenCDE server where the code began under the name Kontroll, uses it at a fixed
> commit, and [Dataspatium](https://github.com/marwiss/Dataspatium), the app in
> the browser, shows and queries the graphs it makes through Dataregium. How they
> use it is told [below](#how-dataregium-and-dataspatium-use-it).

## Contents

- [In plain words](#in-plain-words), for everyone
- [How Dataregium and Dataspatium use it](#how-dataregium-and-dataspatium-use-it)
- [Quick start](#quick-start), for developers
- How it works:
  [import](#how-a-model-is-imported),
  [the graph](#the-graph),
  [STEP ids](#how-step-ids-are-kept),
  [geometry](#how-geometry-is-stored),
  [where the products are](#where-the-products-are),
  [export](#how-a-model-is-exported),
  [comparing two files](#when-two-files-hold-the-same-model),
  [validation](#validation),
  [pointing at an instance](#pointing-at-an-instance),
  [asking a model](#asking-a-model),
  [changing the graph](#changing-the-graph),
  [a building into another project](#a-building-into-another-project),
  [Neo4j and Memgraph](#neo4j-and-memgraph),
  [IFC5](#ifc5-experimental)
- [Measurements](#measurements)
- [Reference](#reference): the functions and the command lines
- [Background](#background), [Licence](#licence), [References](#references)

## In plain words

**A building model** is the digital version of a building. Architects and
engineers draw it in programs such as Revit, Archicad or Tekla, and share it as
an **IFC** file, the open format that every such program can read and write. An
IFC file is a long list of small records: a wall, a door, the storey they stand
on, the material of the wall, its fire rating, the shape of its body. A large
model has hundreds of thousands of them, and they point at each other.

**A graph database** stores things and the links between them. That suits an IFC
model, which is mostly links: this door is in that wall, that wall is on this
storey and has these properties. In a graph database you can ask questions such
as "which external walls are taller than three metres?" or "what lies within
1.2 metres of this door?", and keep the building next to issues, documents and
other data.

**What IFC–DB does:**

- It **puts an IFC file into the database**, one record at a time, with every
  link kept.
- It **writes the file back** from the database. An unchanged model comes back as
  the very same file, character for character. A model that was changed in the
  database comes back with the change and nothing else.
- It **works out where every part of the building is**, as a box in metres, so
  that questions about distance and position can be asked.
- It **names every part** of the building by its lasting id and the way to it, not
  by the file's numbering, so that a proposed change can say exactly what it
  changes.
- It **brings a building into another project**: a building from one model goes
  into the project of another as a new model, and both stay as they were.
- It **checks a file** against the IFC standard, against buildingSMART's rules
  (buildingSMART is the organisation behind IFC) and against IDS, a format for
  writing down what a model must contain.

**What it is not.** It is not a program you open, a viewer or a server. It is a
building block for those. Most people meet it through Dataregium, a server where
a project's models and issues are shared, or Dataspatium, an app in the web
browser, and never install it themselves.

**Words used below:**

| Word | Meaning |
|---|---|
| BIM | Building information modelling: working with a building as a digital model |
| IFC | The open file format for building models, by buildingSMART; IFC2X3, IFC4 and IFC4X3 are its versions |
| STEP | The text format IFC files are written in; each record has a number, its STEP id, as `#123` |
| GlobalId | The lasting id IFC gives the main parts of a model, such as walls and storeys |
| Graph database | A database of nodes (things) and relationships (links between them) |
| LPG | Labelled property graph: the kind of graph Neo4j and Memgraph keep, where each node has labels that say what kind of thing it is, and both nodes and relationships hold their values as named properties. The library was first named IFC–LPG, for turning IFC into such a graph, and is now IFC–DB, since it is to keep models in more kinds of database |
| Neo4j, Memgraph | Two graph databases; IFC–DB works the same on both |
| Cypher | The question language of both databases |
| IFC5 | The next version of IFC, still a draft, written as JSON |

## How Dataregium and Dataspatium use it

**In short:** IFC–DB is the part that turns building models into a graph and
back again in two other projects by the same author. People use those two; they
seldom see IFC–DB itself.

- **[Dataregium](https://github.com/marwiss/Dataregium)** is a server where a
  project keeps its building models, documents and issues. It follows OpenCDE,
  buildingSMART's open rules for such servers, so that other programs can use it
  too. When someone uploads an IFC model, Dataregium uses IFC–DB to check the
  file and to put it into its graph database. From there it links the issues to
  the walls and doors they are about, answers questions about the model, keeps
  every version of it, writes any version back as a file, and makes the 3D view.
- **[Dataspatium](https://github.com/marwiss/Dataspatium)** is an app in the web
  browser. It connects to Dataregium, shows the models in 3D, asks questions of
  the graph, such as which parts lie near a given part, compares two versions,
  and proposes changes that Dataregium tries and a project manager approves.
  Dataspatium does not run IFC–DB itself; it reads the graphs that IFC–DB made
  on the server.

**In detail:**

- **Dataregium** installs IFC–DB from this repository at a fixed commit and moves
  it when a new one is needed. The server's own process uses the graph side,
  which needs no IfcOpenShell; worker processes with the extra `ifcopenshell` read,
  convert, write and check files. Its
  [IFC-DB page](https://github.com/marwiss/Dataregium/blob/main/docs/en/ifc-lpg.md)
  describes how. On upload it checks the file against the IFC schema,
  buildingSMART's rules and IDS (`validation`, `gherkin`, `check_ids`), converts it
  in a worker and imports the records (`convert_file`, `import_records`), with one
  `Model` per document version. It joins the components of BCF viewpoints to
  `Entity` nodes by GlobalId, gives its members named Cypher queries over the
  forward relationships, and writes a version back with `export_records` and
  `write_file`. A proposed change, a BIM snippet in a BCF issue, names what it
  changes by GlobalId and path; Dataregium resolves it with `resolve` against the
  version it was made on, keys it with `canonical_path`, and compares two changes
  by the nodes they resolve to. After a conversion it runs the geometry worker
  (`python -m ifc_db.spatial`), puts the boxes into its graph (`import_spatial`)
  and builds its glTF from the worker's file. In its local mode it keeps a
  working copy in Memgraph beside the Neo4j archive, in the same form.
- **Dataspatium** uses IFC–DB only through Dataregium. It reads the graph's form
  directly with the server's graph queries: the labels, `position`, `__type`,
  `__step`, `StepBlock`, and `form_version` in its list of models. Its query of
  the parts near a part uses `bbox_centre`, and a change it proposes names its
  instances by GlobalId and path.
- **Versions.** Dataregium and Dataspatium 2 have the IFC graph and its links to
  issues; version 3 adds the graph that is written back, the versions and change
  sets, the comparison of two versions, the glTF from the server and the boxes.
  The two projects are released together, and each version of one works with the
  same version of the other.

## Quick start

**In short:** this part is for developers who want IFC–DB in a program of their
own. Whoever uses Dataregium or Dataspatium needs none of it.

You need Python 3.11 or later and a Neo4j 5.26 or Memgraph 3.14. For a first try,
either runs in Docker:

```sh
docker run --rm -p 7687:7687 -e NEO4J_AUTH=neo4j/change-this-password neo4j:5.26-community
docker run --rm -p 7687:7687 memgraph/memgraph:3.14.0    # or this one; no user, empty credentials
```

Install IFC–DB from this repository. The extra `ifcopenshell` is needed to
convert, write and check files, and the extra `neo4j` brings the official driver:

```sh
pip install "ifc-db[ifcopenshell,neo4j] @ git+https://github.com/marwiss/IFC-DB@v1.0.0"
```

Put a file in, ask a question, and write it back:

```python
import asyncio
import os
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ifc_db.files import export_file, import_file
from ifc_db.graph import Neo4jGraph, ensure_schema


async def main() -> None:
    auth = ("neo4j", os.environ["NEO4J_PASSWORD"])
    async with AsyncGraphDatabase.driver("bolt://localhost:7687", auth=auth) as driver:
        graph = Neo4jGraph(driver)
        await ensure_schema(graph, "neo4j")
        model = await import_file(graph, Path("house.ifc"), "house")
        print(model["entities"], "nodes")
        walls = await graph.run("MATCH (w:IfcWall {model_id: $model}) RETURN w.Name AS name", model="house")
        print(walls)
        await export_file(graph, "house", Path("house-again.ifc"))


asyncio.run(main())
```

With Memgraph the driver is the same: use `auth=("", "")` for a Memgraph without
users, and `ensure_schema(graph, "memgraph")`. `Neo4jGraph` is named after the
driver, not the database.

To know where the parts are, add the boxes inside `main()`, once the model is in:

```python
from ifc_db.spatial import add_spatial

await add_spatial(graph, Path("house.ifc"), "house", dialect="neo4j")
near = await graph.run(
    "MATCH (d:IfcDoor {model_id: 'house'}), (n:Entity {model_id: 'house'}) "
    "WHERE n <> d AND point.distance(n.bbox_centre, d.bbox_centre) < 1.2 RETURN n.Name AS name"
)
```

Three tools work on files alone, without a database. Each answers in JSON and
exits with 0 when all is well:

```sh
python -m ifc_db.equivalence first.ifc second.ifc          # do the two files hold the same model?
python -m ifc_db.validation model.ifc                      # issues against the IFC schema
python -m ifc_db.validation copy.ifc --against model.ifc   # only those the original does not have
python -m ifc_db.ids model.ifc requirements.ids            # what fails an IDS, and each specification's verdict
```

buildingSMART's rules need an environment of their own, with IfcOpenShell 0.8.6.
The installer makes one in an empty folder on Linux or Windows, from downloads it
checks against pinned digests:

```sh
python -m ifc_db.gherkin_install rules          # the rules and their IfcOpenShell, in the folder rules
python -m ifc_db.gherkin model.ifc --env rules  # their verdict on a file, in JSON
```

The [reference](#reference) lists every function and command line.

## How a model is imported

**In short:** IFC–DB reads the file itself, checks that it is whole, and copies every
record into the database. A damaged file is refused, not loaded in part.

**In detail:**

1. **The file is read by IFC–DB's own STEP reader.** IfcOpenShell supplies the
   schema's tables and nothing else. Its own parser changes some values on
   reading, such as `.NULLENUM.` to `$` and `($,$)` to `()`, which files from
   Tekla and Archicad contain, so a round trip through it could not give the
   same file back. The reader refuses what it cannot read: a truncated file is an
   error, not a shorter model. Its patterns cannot backtrack, and nesting is
   limited.
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
   as identifiers before they reach a query.

While a model loads, its `Model` node is `running`. Afterwards it is `done`, with
its counts, or `failed`, with the error and nothing else of the model left. A
model id that is taken is refused. Several models share one database, told apart
by `model_id`.

The import keeps the model's record itself. `import_records` makes the `Model` if
it is not there, sets what the records say of it, and sets `imported_at` when the
last record is in. A model that is taken out loses it again. So a model with
`imported_at` is all in the graph, and that is what the export, the boxes, the
merge and the named queries ask for. A program that runs the steps itself needs
no state of its own on the node. `model_facts(graph, model_id)` reads the record:
the facts of the import, what the boxes, a preparation and a merge have added,
and `whole`. A model from before `imported_at` is whole when its state is `done`.

## The graph

**In short:** every record of the file becomes a node, named after its kind, such as a wall
or a door, and every link between records becomes a relationship. IFC–DB's own
bookkeeping has names that do not begin with `Ifc`, so the two are not mixed up.

**In detail:**

Every instance that is a node is an `Entity` with its class and all its
supertypes as labels:

```text
(:Entity:IfcWallStandardCase:IfcWall:IfcBuildingElement:IfcElement:IfcProduct:IfcObject:IfcObjectDefinition:IfcRoot)
```

The labels that begin with `Ifc` are IFC's classes. IFC–DB's own labels never
do:

| Label | What it is |
|---|---|
| `Entity` | an instance of the file |
| `Model` | one per model: `format` (`step` or `ifcx`), `schema` (IFC's version, as `IFC4` or `IFC5`), `form_version` (the version of the graph's shape, now 2), `geometry` (the form), the state, `imported_at` (when the import was done), the counts, the file name and the file's header |
| `StepBlock` | STEP text kept in a block, linked from its `Model` by `HAS_STEP_BLOCK` |
| `Object` | an object of an IFC5 file |

Models of both formats share one series of ids.

| Property of an `Entity` | Meaning |
|---|---|
| `id` | `<model id>:<STEP id>`, unique |
| `model_id`, `step_id` | the model, and the number of the instance in the file, as in `#123` |
| `ifc_class` | the concrete class |
| `GlobalId`, `Name`, `Description`, `Tag`, … | every literal attribute, by its IFC name |
| `bbox_min`, `bbox_max`, `bbox_centre` | the product's box, when it has been [worked out](#where-the-products-are) |
| `elevation_m`, `elevation_source` | on a storey: its height in metres, from its `Elevation` (`attribute`) or, where the file leaves that out as IFC4X3 files may, from its placement in the project's coordinates (`placement`); none when the file's length unit is not known |

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
| A list the schema gives rows of exactly two or three REALs, such as `CoordList` | A list of 2D or 3D Cartesian points |
| Another list of lists of numbers | JSON text, since Neo4j has no nested lists |
| What has no plain form, such as a list of typed values | Tagged JSON, with `$json` as its type |
| An instance the schema does not know | All its values in `ifc_args` |
| The header | `ifc_header` on the `Model` node |
| An instance in a block | STEP text in a `StepBlock` node |

An `IfcOwnerHistory` is linked from its `IfcProject` only. Linked from every
instance that shares it, it would become the centre of the graph. Names of
IFC–DB's own properties, such as `ifc_class` or `bbox_min`, are in lower case
with underscores, so they cannot be mistaken for IFC's attributes.

## How STEP ids are kept

**In short:** every record keeps its number from the file, so the file can be written
back with the same numbers, and programs that rely on them keep working.

**In detail:**

Every node keeps the number its instance has in the file, in `step_id`, and its
`id` is `<model id>:<STEP id>`. A reference to an instance that is no node,
because it is in a block, keeps that instance's number in `<attribute>__step`.
The writer gives every instance its number back.

So the written file has the same instances with the same ids, in the same order
and with the same values. Native IFC tools that merge by id, such as
[ifcmerge](https://github.com/brunopostle/ifcmerge), depend on that.

## How geometry is stored

**In short:** the shapes are most of a file. By default IFC–DB makes nodes of what is
useful to ask about and to change, such as the kind of shape, its depth, its
colour and its layer, and packs the many small parts, such as points, triangles
and curves, as text.

**In detail:**

Geometry is most of a model, and most of it is what shapes are built of. Three
forms are on offer, and the `Model` says which one a model has in `geometry`.

- **The skeleton**, the default. The shapes of products are nodes, with their
  representations and types, solids and boolean results, mapped items and the
  definitions they share, profiles, placements, contexts, the items that give a
  shape its style, styles, colours and layers, so that every style can be
  edited. Points, directions, point lists, meshes and their faces, B-rep faces
  and loops, and curves are kept as STEP text in blocks, as are owner histories
  other than the project's.
- **Blocks**, with `geometry_in_blocks=True`: all geometry is text, for the
  smallest graph and the fastest import.
- **Nodes**, with `include_geometry=True`: every instance is a node.

A block holds at most 256 KiB of STEP, deflated and in base64, since a string is
the one thing both databases store alike. The blocks are drawn by class, not by
product, because 17 to 94 per cent of the geometry in the sample models belongs to
more than one product, and some of it to none.

The skeleton answers which kind of body a product has, how deep an extrusion is
and what its profile is, which definitions products share, which style a shape has
and on which layers it lies. Its values are in the file's units, and its
coordinates are relative to placements, so it does not say where something is:
the [boxes](#where-the-products-are) do.

Where a node holds a list the schema gives rows of exactly two or three REALs, the
list is points that both databases store alike and their point functions read:
the coordinates of a mesh (`CoordList`), and its normals, texture coordinates and
colours. In the skeleton those lists are in blocks; in the nodes form they are
points. The schema decides, not the values, so an attribute is points on every
node or on none; a row the schema does not allow stays JSON text.

What is in a block is in the graph but out of Cypher's reach, and it cannot be
changed there. A reference from a block to a node is not known to the database
either, so deleting a node that a block refers to leaves a reference to nothing
in the written file, which validation reports. Typed queries took about as long in
every form in the [measurements](#measurements), so the reason to keep geometry in
blocks is what it costs to store and import, not what it costs to ask.

## Where the products are

**In short:** a file only says where things are relative to each other. IFC–DB works out
a box in metres around every part of the building, so that you can ask what lies
near what and which parts may collide, and it prepares the shapes for a 3D view.

**In detail:**

The coordinates in a file are local: an element is placed relative to its
storey, the storey to the building, and so on, and a shape is often placed again
through a mapped item. In the renovation model of the
[measurements](#measurements), 64 of the 213 points are the origin, and 42 of
its 43 placements are relative to another.

A pass of IfcOpenShell's geometry, `add_spatial`, answers where. It builds every
product's shape once and puts a box around it in the project's coordinates,
IFC's "world", in metres: `bbox_min`, `bbox_max` and `bbox_centre` on the
product's node, as 3D Cartesian points, with a point index on the centre. The
same Cypher then asks on both databases what lies within 1.2 m of a door, what has
its centre in a box, or which boxes overlap a door's.

The boxes follow the world's axes, not the element's, so they suit selection and
nearness, not exact geometry. They are derived, not part of the file: the writer
reads only the schema's attributes, so the file comes back the same with them or
without them, and a new pass replaces them. Every product with a body is boxed,
openings and spaces included; one whose shape cannot be built is counted and
named, not boxed. The IFC schema and buildingSMART's rules let such a shape
through, such as a profile 0 m wide, so `geometry_issues(records)` gives those
products as issues in the form of a validation report, with their GlobalIds, for
the report of the file. Boxes from another file are refused: every product must be a
node of the model with the same GlobalId. With two threads the pass took about 2 s for
a Revit model of 286 products, 4 s for a Bonsai model of 39 products with large
meshes, and 154 s for a Tekla model of 10,042 products.

With `meshes=True`, `add_spatial` also puts each product's mesh on its node, in
the project's coordinates and in metres: `mesh_points`, a list of 3D points, and
`mesh_faces`, three indices into them for each triangle. Cypher can then ask about
every corner of a shape, such as what reaches above a slab. They are long lists, so
they are off by default, and a pass without them takes them away.

`overlapping_boxes(graph, model_id)` lists the pairs of products whose boxes
overlap, within one model or, with a second model id, between two models whose
boxes are in the same coordinates. A `clearance` in metres also takes in the
boxes that come that near, a negative one leaves out the boxes that only touch,
and `exclude` leaves out classes such as `IfcOpeningElement`. The point index
finds each product's candidates, so the whole model is not compared pair by
pair. The pairs are the candidates for a clash test: boxes that do not overlap
show that two parts cannot collide, but boxes that do overlap do not show that
the parts do. The exact test is made on the shapes, in a file. A model that
exists only in the graph is written out first.

The pass also writes a file of its own, which a server can turn into glTF for a
3D view: each mesh once, in its own coordinates, and per product its storey, its
mesh, the matrix that places it, column by column as glTF has it, and its box. A
shape that several products share is one mesh placed several times. Dataregium
builds its 3D view from that file.

## How a model is exported

**In short:** the file is written from the database, so a change made there is in the
file, and what IFC–DB added for its own use stays out.

**In detail:**

The file is written from what the graph holds, not copied: the nodes in order of
STEP id with their properties and relationships, the instances in the blocks, and
the header from the `Model`. A property or a relationship changed in the graph
therefore shows up in the file.

The writer reads only what the schema says is an attribute, so inverse
relationships, boxes and whatever else has been added to the graph stay behind, a
date or bytes on a node as well. An attribute that holds what IFC has no value for
stops the writing, with an error that names the instance and the attribute. A file
is written a line at a time from the graph's pages, in order of id, so its size
does not decide the memory it takes. An export stops if the blocks do not hold
the number of instances the conversion put in them, since a file without them
could not be told from a right one.

## When two files hold the same model

**In short:** IFC–DB can tell whether two files describe exactly the same model, even
when their records are numbered or ordered differently.

**In detail:**

A comparison says whether two files hold the same model: there is a one-to-one
pairing of their instances in which paired instances have the same class and the
same values, their references lead to paired instances, every LIST and ARRAY has
its order, and every SET and BAG has the same members. All instances count, not
only those reached from an `IfcRoot`, since a map conversion is reached from
none.

There is no tolerance. A REAL is compared by its bits, since a double survives
text, JSON and Bolt exactly when it is written in its shortest form. *Identical*
asks for the same ids, the same order in SETs and the same header too.

The comparison reads the files with IFC–DB's own reader, and checks a pairing it
has built against the files alone, so a defect can give a wrong "not the same"
but not a wrong "the same". With `--geometry` it also builds every product's mesh
with IfcOpenShell and compares the meshes bit for bit, and it names the
georeference in its report.

A check took about 10 s for 224,496 instances and 19 MB, single-threaded. On a
model with many instances that look alike the search costs more. It is bounded,
and reaching the bound gives no answer, which counts as not the same.

## Validation

**In short:** a written file can be checked against three sets of rules: the IFC standard,
buildingSMART's rules for good models, and requirements a project has written
down in IDS.

**In detail:**

A graph database has no schema that could refuse a wrong value, and the rules of
IFC are written in EXPRESS, which no graph database holds. So the file is
validated, not the graph:

- **The IFC schema**, with IfcOpenShell's validator and the rules of the EXPRESS
  schema: the type and number of attributes, enumerations, the cardinality of
  inverse attributes, the form and uniqueness of GlobalIds, and WHERE rules and
  global rules. Files in use often break a rule already, so after a change what
  counts is an issue the original file does not have (`--against`, which takes
  several files).
- **buildingSMART's
  [ifc-gherkin-rules](https://github.com/buildingSMART/ifc-gherkin-rules)**, the
  normative rules and industry practice of buildingSMART's validation service.
  They run as the rule set's own tests run them, in an environment of their own
  with IfcOpenShell 0.8.6, and without the rule set's Django parts, which only
  write results to the validation service's database. On the rule set's 722
  test files this gives the expected verdict for every one. Each finding keeps
  its severity, and the report counts errors apart (`error_count`), since
  buildingSMART's service holds a file valid when all it finds are warnings. At
  the pinned commit GEM004 does not run on IFC2X3 files, GRP001 fails on subtypes
  of `IfcGroup` and PSE001 on some files; a rule that raises an exception is
  listed under `rules_not_run`, apart from the findings.
- **Information Delivery Specifications**, buildingSMART's IDS 1.0, with
  IfcOpenShell's IfcTester and its own defaults. An IDS is read with
  defusedxml, which refuses DTDs and entities, and checked against IDS 1.0's
  schema built without the network. Every requirement an element fails is an
  issue, and so is a required specification that applies to nothing, and an
  element that a prohibited one applies to. A summary gives each
  specification's verdict.

## Pointing at an instance

**In short:** to change one part of a model you have to say which part. IFC–DB names it
from the nearest part with a lasting id, its GlobalId, and the way from there.

**In detail:**

A proposed change to a model, such as a BIM snippet in a BCF issue, has to say
which instance it changes, in a way that still holds after other changes.

- **A GlobalId and a path.** The path starts at the nearest instance with a
  GlobalId and follows IFC's attribute names and list positions, such as
  `IsDefinedBy[0].RelatingPropertyDefinition`. It goes backward through IFC's
  inverse attributes by their own names:
  `Representation.Representations[0].Items[0].StyledByItem[0].Styles[0]` is a
  wall's style. A name on a class is either forward or inverse, so a path leads
  to one node or to none. An inverse attribute is a set, so its members are
  ordered by what they hold, not by STEP id. A forward `[0]` may be left out.
- **The STEP id** names only what no GlobalId reaches.
- **A change to a shared instance** applies to every instance that shares it.
  Only an explicit copy changes one alone.

`resolve(graph, model_id, global_id, path)` in `ifc_db.paths` follows a path to
its node. `canonical_path(graph, model_id, step)` gives a node the one path it is
named by when several reach it: the shortest, forward steps before inverse ones,
then the names in alphabetical order, the lowest position and the lowest
GlobalId. Nothing is stored: any path that reaches a node resolves to it. Both read
the graph as it is, so a change is resolved before it is made. In the skeleton a
path can reach an instance kept in a block, such as a mesh, and goes on from it
only backwards, to what a node says about it: from a mesh to its `StyledByItem`
and its `LayerAssignment`, so that the style of a mesh can be named and changed
too.

The approach was tried on 28 sample files of up to 224,496 instances. Paths
forward from a GlobalId reached 87 to 99.5 per cent of the instances, and with
steps backwards 98 to 100 per cent. The 0 to 1.9 per cent that no path reaches
are unused points and placements, which only their STEP id names. `unreachable(graph,
model_id)` lists the nodes no path reaches, so that a check can warn of them and
a change can remove them; a model with them is imported all the same. A node that
a block refers to counts as reached, since the database does not know that
reference, and instances kept in blocks are not listed.

## Asking a model

**In short:** the usual questions to a model have names. A program asks by name
and gets rows back, without writing Cypher and without knowing the graph's form.

**In detail:**

`ifc_db.queries` holds nine named queries on a model:

| Name | Answers | Parameters |
|---|---|---|
| `entity` | One rooted entity by its GlobalId: its class, its literal attributes, and what it refers to and is referred to by | `global_id` |
| `element-classes` | How many elements the model has of each class | |
| `elements` | The elements of a class, with the spatial structure that contains each | `class` |
| `storeys` | The storeys, with their elevation as the file gives it, their height in metres and where it comes from, and how many elements and spaces each holds | |
| `spaces` | The spaces, with their storey and their net and gross floor areas | |
| `areas` | The net floor area of each storey's spaces, added up, with the storey's height | |
| `materials` | The materials of each element: its own, or those of its layers, list or constituents | `class`, optional |
| `property` | The value of one property of a property set on every object that has it, with the value's IFC type | `pset`, `property`, and `class`, optional |
| `site` | Where the model stands: each site's latitude, longitude and elevation as IFC has them, the latitude and longitude in decimal degrees, and the map projection with its eastings, northings, height, rotation and scale | |

- **Asking.** `run_query(graph, model_id, name, parameters)` gives `{"name",
  "columns", "rows", "truncated"}`. The rows are lists in the order of the
  columns, of JSON values. There are at most `max_rows` of them, 10,000 unless
  another number is given, and `truncated` says that there were more. A query
  has `timeout_seconds`, 10, to run.
- **A class takes its subclasses with it:** `IfcWall` finds an
  `IfcWallStandardCase` too.
- **The parameters are checked before the graph is asked.** A class must be the
  name of an IFC class, a GlobalId 22 characters of IFC's base 64, and any other
  parameter 1 to 255 characters without control characters. `InvalidParameters`
  says which one does not fit, and `UnknownQuery` that there is no query by that
  name.
- **The list.** `catalogue()` gives the queries as plain data, each with its
  name, description, parameters and columns, for a server to show.
- **One property for many parts.** `property_values(graph, model_id, pset=…,
  property=…, global_ids=[…])` looks a property up for up to 1,000 GlobalIds. It
  gives a row for each one the model has, with the value and its IFC type, or
  nothing in them where the part lacks the property.

Every query names its model by id, takes values only as parameters and only
reads. A model that is not there, or whose import is not done, has no rows. The
time limit is the caller's own clock. A graph that can tell the server to stop a
statement offers `read_rows(statement, params, timeout_seconds=…)`, and the
queries use it when it is there.

## Changing the graph

**In short:** a model can be changed in the database before it is written back.
IFC–DB helps a program that does so to keep the graph right: it says what
refers to a part, it deletes a part only when nothing else refers to it, it puts
the links that run backwards right after a change, and it lists what nothing
reaches.

**In detail:**

The writer reads the forward relationships, so a change made to one of them
reaches the file. The inverse relationships are there for queries, and a change
to a forward relationship leaves them as they were. `refresh_inverses(graph,
model_id, steps)` in `ifc_db.editing` makes them anew for the nodes whose forward
relationships changed, exactly as the import makes them, from a table of the
inverse attributes of IFC2X3, IFC4 and IFC4X3 that needs no IfcOpenShell.

`referrers(graph, model_id, step)` lists what refers to an instance: the nodes,
with the attribute and position, and the instances kept in blocks, whose
references the database does not know. A node with referrers should not be
deleted on its own, since the written file would then refer to nothing, so
`delete_nodes(graph, model_id, steps)` deletes nodes only when nothing outside them
refers to them, and otherwise raises `StillReferred` with what does and deletes
nothing. `unreachable(graph, model_id)` in `ifc_db.paths` lists the nodes that no
GlobalId reaches, which a change can remove. Dataregium uses these in its change
sets, which are checked and approved before they become a new version.

## A building into another project

**In short:** a building from one model can be put into the project of another,
for example an annex drawn on its own into the model of the whole site. The result
is a third model, and the two it came from do not change. Where both models have
the same part, the same lasting id, IFC–DB says so, and the person merging
chooses which version to keep. The new model is checked against the IFC standard
before it is ready.

**In detail:**

`merge_report(graph, base_id, other_id, building)` in `ifc_db.merge` says what
bringing the building `building`, a GlobalId, of the model B into the project of
the model A would do, and `merge_building(graph, base_id, other_id, building,
model_id)` does it, as the new model C. Both work in the graph and need no
IfcOpenShell; the check at the end does.

- **What comes along:** what lies below the building, through aggregation,
  nesting, containment, openings, fillings, coverings and projections; the
  relationships that concern it, with the types and property sets they define; the
  styles and layers of its shapes; and everything those refer to. A relationship
  that also concerns something outside the building keeps only what is inside. One
  that points outside alone, such as a group, a system or a connection to another
  building, is left out, and the report lists it.
- **Where it stands:** the building stands below A's site as it stood below its own,
  and its shapes use A's representation contexts where A has the same ones; a
  context A lacks is added to A's project. `site` names A's site when A has more
  than one.
- **The same part in both:** a GlobalId names one object, whatever its STEP id.
  Where B's object has the same content as A's, with what it refers to, C has A's.
  Where they differ, the report lists it with `same` false, and a choice is needed
  before anything is made: `"a"` keeps A's, and B's relationships keep it only
  where they also concern something else of B; `"b"` puts B's in its place, and
  A's relationships that concern it alone go; `"new"` makes two objects, B's with
  a new GlobalId. `choices` maps a GlobalId to its choice, and `default` holds for
  the rest. Without them, `MergeConflict` carries the report.
- **What stays the same:** an object that is A's in C is placed by A's placement
  of it, and what A placed relative to a replaced object is placed relative to
  B's. An object of B's own, new or in place of A's, joins the relationships of A
  that B has as well, such as a shared property set, type or material, in their
  lists, for IFC2X3 lets a property set or a type have only one such relationship.
  What A's replaced objects, and the relationships that go with them, held alone
  goes too. An application, which IFC keeps unique by its identifier, is A's.
- **The numbers:** C keeps A's STEP ids, and the instances from B are numbered
  after them.
- **The check:** C is `running` until it has been written out and validated
  against A and against B numbered as C numbers it. An issue that neither has
  takes C out again and makes it `failed`, with the issues in the error. A choice
  that gives an object two places, such as two storeys, is caught there.

The schema and the form of the geometry must be the same in A and B, and so must
every unit of a kind that both have. A unit of a kind that A has none of, such as
an area unit in a model that gives only a length, comes along into A's units in C,
so that B's values keep their meaning, and the report lists it under
`units_added`.

Models of two schemas are not merged as they are: one is converted to the other's
schema first, the conversion is checked, and only then are they merged. Where B's
schema or its length unit differ, `prepare_other(graph, other_id, prepared_id,
base_id=…)` makes B ready as a new model: a worker migrates it to A's schema with
IfcOpenShell's `Migrator`, through IFC4 between IFC2X3 and IFC4X3, and turns its
lengths into A's unit, such as millimetres into metres. Units are compared by what
they mean, their kind, SI unit and scale, so Revit's `DEGREE` and Bonsai's
`degree` are one; any other unit that A has another of is refused with what it is.

- **What cannot be converted** is not dropped or changed without a choice:
  `PrepareConflict` carries a report of each problem, a class the other schema has
  no equivalent of or what it needs that B lacks, with how many there are, some of
  them, the products they belong to, and the options there are. `resolutions`
  chooses one per problem, from `RESOLUTIONS`: `type` makes an IfcWindowStyle or
  IfcDoorStyle an IfcWindowType or IfcDoorType, `proxy` makes an element a proxy,
  `leave_out` takes the objects out with their relationships, `polyline` makes an
  IfcIndexedPolyCurve of straight segments an IfcPolyline, `virtual` gives a space
  boundary without a building element an IfcVirtualElement, `dated` gives an owner
  history its date of change, `assignment` puts the styles of a styled item in the
  style assignment IFC2X3 needs, `context` gives a project without a
  representation context a 3D model context at the origin, and `owner` gives what
  IFC2X3 needs an owner history on one made by IFC–DB. A problem without options, such as the meshes of IFC4 in
  IFC2X3, cannot be solved here. The report lists the problems of every step at
  once.
- **The check:** B and the converted model are both validated, B's issues are
  carried over to the new numbering by instance and attribute, and an issue that B
  did not have stops it: `PrepareError` lists the new issues in the form of a
  validation report, and no model is made. A model that is made says so in
  `prepared_check`, with `prepared_from` and `prepared_resolutions`.

IfcOpenShell reads and writes the file there, so the prepared model is a conversion,
not the same file. On real models, Revit's Duplex (IFC2X3) became IFC4X3 with its
window and door styles as types and its virtual space boundaries given virtual
elements, and the check passed. The boxes are not copied, so the geometry pass is
run again for C.

## Neo4j and Memgraph

**In short:** the two databases differ in small ways. IFC–DB uses only what both do
alike, so either can be chosen, and the same questions work on both.

**In detail:**

The Cypher is parameterised and portable: no APOC, and no `elementId()` or
`id()` as keys. The same graph and the same Cypher work on both, and a model
comes back identical from either. The tests run on Neo4j 5.26 and Memgraph 3.14,
on Python 3.11, 3.13 and 3.14.

`ensure_schema(graph, dialect)` makes the unique keys, the indexes and the point
index in either database's words, and leaves those that exist. Where the two
differ, IFC–DB keeps to what both do alike:

- Neo4j stores no maps, nested lists, mixed lists or nulls in lists, so such
  values are JSON text.
- A point's `crs` is named differently, so queries compare `srid`.
- Memgraph 3.14 does not keep a point index up to date: a point set after the
  index was made is not found through it. Given `dialect="memgraph"`,
  `add_spatial` and `import_spatial` make the index anew when the boxes are in
  place; Neo4j needs nothing.

## IFC5 (experimental)

**In short:** IFC5, the next version of IFC, is still a draft. IFC–DB can already read
and write its files, and check that their values are of the kinds their schemas
say, on trial.

**In detail:**

IFC5 is a draft. Its files, IFCX, are JSON in the format `ifcx_alpha`, which
can still change, and so can this part of IFC–DB. A file is a list of entries,
each about a path: its children and what it inherits, by name, and its
attributes under namespaced keys.

One or more files are composed into one opinion per path. A file comes after
the files it imports, and each counts once. A later value for the same path and
name wins, `null` takes one away, and two values that disagree are reported, as
is an import that is none of the files: it is not fetched.

Each path becomes an `Object` node, labelled with its class when it has one, in a
`Model` with `format` `ifcx` and `schema` `IFC5`, which holds the header, the
imports and the schemas. Children and what is inherited are `CHILD` and
`INHERITS` relationships with a `name`, and a `{"ref": path}` value is a `REF`
relationship as well. The graph is the same on both databases, so an object's
members are properties under dotted keys, as `bsi::ifc::class.code`; rows of two
or three numbers, as the points of a mesh, are lists of points; a 4×4 transform
is a flat list with its shape beside it; and what does not come back the same is
JSON text.

The round trip gives back the same composition: every path the same children,
the same inherited definitions and the same attributes, with the same JSON
types, an integer as an integer and a float to the bit. The file is written with
one entry per path. Instance paths through children, as `<path>/Window_001`, are
kept as paths of their own, not resolved. A file of more than 256 MB, one that
nests deeper than 32, and one that is no JSON or has numbers JSON does not
have, such as `NaN`, is refused.

A change made in the graph can be written as a layer of its own, as IFC5 means
layers to be used: `export_ifcx_overlay(graph, base_model_id, model_id, path)`
writes what the model changes against the base, which imports the files the base
was composed of and holds only the paths, children, inherited definitions and
attributes that differ, with `null` for what was taken away. Composed after those
files, the layer gives the changed model. IFCX has no way to take away a path, so
a path that is gone keeps an entry with all its parts set to `null`.

An IFCX file says what its attributes are in `schemas`, and imports more schemas
by their URI. `python -m ifc_db.ifcx_check FILE... --schemas DIR`, or
`check_ifcx([paths], schemas=DIR)` in `ifc_db.ifcx_check`, composes the files and
checks every attribute against the schema for its key: its data type, the options
of an enumeration, every member of an array, and the members of an object, of
which only those marked optional may be missing. The issues come in the form of a
validation report. The imported schemas are not fetched while a file is checked:
`python -m ifc_db.ifcx_check --fetch DIR` fetches the ones IFC–DB has pinned,
those that buildingSMART's examples import, each checked against its SHA-256. An
import that is not pinned is a warning, and so is an attribute without a schema
while such an import is there. buildingSMART's examples "Hello Wall" and the
georeferenced bridge deck give no issue.

## Measurements

**In short:** what was tried, on which real models, and how large and fast it was.

Without a database, all 26 whole files among the samples came back identical, up
to 224,496 instances and 19 MB, from Revit, Archicad, Tekla, SketchUp, SierraSoft
and IfcOpenShell. IfcOpenShell's own written form of original and copy was equal
on every one, and the generated meshes were equal bit for bit, up to 4,134
products in one model. Six truncated files, and one in a schema IfcOpenShell does
not have, were refused.

Five real models went through a fresh database of each kind and all came back
identical. This was measured before the skeleton became the default, in three
forms: *ordinary*, without geometry, which cannot be written back; *blocks*; and
*nodes*.

| Model | File | Instances | Neo4j store, MB: ordinary / blocks / nodes | Memgraph memory, MB: ordinary / blocks / nodes | Import on Neo4j, s: ordinary / blocks / nodes |
|---|---|---|---|---|---|
| SketchUp, tessellated | 5.6 MB | 23,528 | 2.3 / 3.9 / 19.6 | 13 / 17 / 48 | 3 / 3 / 12 |
| Archicad | 2.8 MB | 49,664 | 6.6 / 8.3 / 21.0 | 26 / 27 / 55 | 13 / 13 / 32 |
| Archicad | 6.0 MB | 110,176 | 11.3 / 14.6 / 42.8 | 37 / 39 / 95 | 20 / 15 / 53 |
| SierraSoft, road | 14.6 MB | 127,491 | 4.8 / 10.6 / 54.7 | 21 / 27 / 130 | 9 / 7 / 38 |
| Tekla | 19.2 MB | 224,496 | 55.8 / 108.9 / 137.2 | 144 / 149 / 264 | 58 / 32 / 94 |

The skeleton was measured on three other models, over an empty database. What it
costs depends on the program that wrote the file: for Bonsai, which gives every
mesh a style of its own, a fifth of the nodes, and for Tekla, whose skeleton is
most of its instances, nearly as many as the nodes form. It gave the same answers
as the nodes form to eight queries on geometry, on both databases, and every model
came back identical in every form.

| Model | Instances | Nodes: blocks / skeleton / nodes | Neo4j store, MB | Memgraph memory, MB |
|---|---|---|---|---|
| Bonsai, IFC4X3 | 26,222 | 293 / 5,617 / 26,222 | 0.6 / 0.7 / 8.8 | 2.6 / 12.1 / 28.9 |
| Revit, IFC2X3 | 38,898 | 9,786 / 15,331 / 38,898 | 4.1 / 5.6 / 12.3 | 17.6 / 23.4 / 38.0 |
| Tekla, IFC2X3 | 224,496 | 104,918 / 197,985 / 224,496 | 67 / 115 / 124 | 136 / 214 / 237 |

The skeleton's node counts include the items that give a shape its style, which
became nodes in November 2026; its store and memory were measured before, when
they were text in blocks. They are 4,893 instances in the Bonsai model, 267 in the
Revit model and 4,940 in the Tekla model.

Queries from a storey to its elements and through property sets took 7 to 25 and
11 to 90 ms on Neo4j in all three forms, with no pattern among them. This is one
run on one machine with other work going on, on models of moderate size; a model
of millions of instances needs measuring first.

Four pairs of original and written copy, opened in Bonsai 0.8.3 and in FreeCAD
1.0.1 through NativeIFC, gave the same objects, products and meshes for both.
FreeCAD keeps how a REAL is spelled, so one line of one file read `1.0E-5` in the
original and `1.E-05` in the copy, the same number.

## Reference

**In short:** the list of functions and commands, for developers.

The package without extras needs nothing, and holds the graph side: the STEP
reader and writer, the lossless form, importing and exporting records, deleting a
model, the boxes' import, the paths, the named queries, the support for changes,
the merge and IFC5's graph side. A server can run that in its own process and leave
IfcOpenShell to the workers, which need the extra `ifcopenshell`. Everything that reads, converts, writes or checks a file runs in a
process of its own, within `ifc_db.files.Limits`: 900 s, 4,096 MB of memory on
POSIX and 5 million instances, unless others are given.

| To | Use |
|---|---|
| Make the keys and indexes | `ensure_schema(graph, "neo4j" or "memgraph")` in `ifc_db.graph` |
| Put a file in | `import_file(graph, path, model_id)` in `ifc_db.files`; `geometry_in_blocks=True` or `include_geometry=True` for the other forms, `lossless=False` for the ordinary form |
| Write it back | `export_file(graph, model_id, path)` in `ifc_db.files` |
| Take a model out | `delete_model(graph, model_id)` in `ifc_db.importer` |
| Read a model's record | `model_facts(graph, model_id)` in `ifc_db.importer` |
| List the models | `models(graph)` in `ifc_db.importer`: each model's id and whether it is all in the graph |
| Convert or write in steps of your own | `convert_file` and `write_file` in `ifc_db.files`, `import_records` in `ifc_db.importer`, `export_records` in `ifc_db.exporter` |
| Add the boxes | `add_spatial(graph, path, model_id, dialect=…, meshes=False)` in `ifc_db.spatial`; `spatial_file` writes the geometry's file alone, and `import_spatial` reads one in |
| Report the shapes that cannot be built | `geometry_issues(records)` in `ifc_db.spatial` |
| Find the parts that may collide | `overlapping_boxes(graph, model_id, other_model_id=None, clearance=0.0, exclude=())` in `ifc_db.spatial` |
| Follow or give a path | `resolve` and `canonical_path` in `ifc_db.paths` |
| List what no path reaches | `unreachable(graph, model_id)` in `ifc_db.paths` |
| Ask a model a named query | `run_query(graph, model_id, name, parameters)` and `catalogue()` in `ifc_db.queries` |
| Look a property up for a list of GlobalIds | `property_values(graph, model_id, pset=…, property=…, global_ids=[…])` in `ifc_db.queries` |
| See what refers to an instance | `referrers(graph, model_id, step)` in `ifc_db.editing` |
| Delete nodes that nothing else refers to | `delete_nodes(graph, model_id, steps)` in `ifc_db.editing` |
| Put the inverse relationships right after a change | `refresh_inverses(graph, model_id, steps)` in `ifc_db.editing` |
| Make a model ready to be merged | `prepare_other(graph, other_id, prepared_id, base_id=…, resolutions=…)` in `ifc_db.merge` |
| Bring a building into another project | `merge_report(graph, base_id, other_id, building)` and `merge_building(graph, base_id, other_id, building, model_id, choices=…)` in `ifc_db.merge` |
| Put IFC5 files in, and write them back | `import_ifcx(graph, [paths], model_id)` and `export_ifcx(graph, model_id, path)` in `ifc_db.ifcx` |
| Check IFC5 files against their schemas | `check_ifcx([paths], schemas=dir)` in `ifc_db.ifcx_check` |
| Write a change as an IFC5 layer | `export_ifcx_overlay(graph, base_model_id, model_id, path)` in `ifc_db.ifcx` |
| Compare two files | `compare_files(first, second)` in `ifc_db.equivalence` |
| Check against IDS | `check_ids(path, [ids_paths])` in `ifc_db.files` |

The command lines, each in a process of its own:

| Command | Does |
|---|---|
| `python -m ifc_db.worker IFC OUT [--lossless] [--include-geometry \| --geometry-in-blocks]` | converts a file to records |
| `python -m ifc_db.writer RECORDS OUT` | writes records as a file |
| `python -m ifc_db.spatial IFC OUT [--threads T]` | writes the geometry's file: meshes, matrices and boxes |
| `python -m ifc_db.ifcx read FILE... OUT`, `write RECORDS OUT` and `overlay BASE RECORDS OUT --layer-id ID` | reads and writes IFC5 files and layers |
| `python -m ifc_db.equivalence A B [--geometry]` | compares two files |
| `python -m ifc_db.validation FILE [--against SRC...]` | checks a file against the IFC schema |
| `python -m ifc_db.ifcx_check FILE... [--schemas DIR]` and `--fetch DIR` | checks IFC5 files against their schemas, and fetches the pinned ones |
| `python -m ifc_db.prepare IFC OUT [--schema S] [--length-unit U] [--resolve KEY=OPTION...]` | migrates a file, turns its lengths into another unit, and checks the result |
| `python -m ifc_db.ids FILE IDS...` | checks a file against IDS |
| `python -m ifc_db.gherkin_install DIR` and `python -m ifc_db.gherkin FILE --env DIR` | buildingSMART's rules |

Every command but the installer also takes `--memory-mb N`. Dataregium's
[IFC-DB page](https://github.com/marwiss/Dataregium/blob/main/docs/en/ifc-lpg.md)
describes how a server uses the parts.

## Background

**In short:** where the method comes from, and why it was written anew.

In 2023 this repository held an implementation of the IFC-graph method of Zhu,
Wu and Lei, rewritten for the official Neo4j driver for Python after py2neo was
discontinued. The method maps an IFC model onto a labelled property graph: one
node per instance, the class hierarchy as labels, literal attributes as
properties, and attributes that point at other instances, inverse attributes
included, as relationships named after the attribute.

Two limits of the method matter as soon as the graph is to be more than a copy
for reading. The article's graph loses the order of lists, since a relationship
does not say where in a list its target stood, and it turns all geometry into
nodes, which the article names as a cost in size. Neither lets the file be
written back. The article also merged every node and relationship, which searches
the graph for duplicates and slows down as the graph grows.

In 2026 the conversion was written anew in Dataregium, then named Kontroll, where
it joins the components of BCF viewpoints to the building elements they point at.
There it gained what a round trip needs: list positions, typed values, the
header, geometry kept as text, a STEP reader and writer of its own, validation,
and a comparison that says whether two files hold the same model. This
repository carried that work on under the name IFC–LPG, IFC as a labelled
property graph, and in 2026 it was renamed IFC–DB, since it is to keep models
in more kinds of database.

## Licence

IFC–DB is free software under the GNU Affero General Public License, version 3
or later, in [LICENSE](LICENSE). It is the licence of Dataregium, where the code
was written.

## References

Junxiang Zhu, Peng Wu and Xiang Lei: "IFC-graph for facilitating building
information access and query". *Automation in Construction* 148 (2023), 104778.
[doi:10.1016/j.autcon.2023.104778](https://doi.org/10.1016/j.autcon.2023.104778),
open access under CC BY 4.0.
