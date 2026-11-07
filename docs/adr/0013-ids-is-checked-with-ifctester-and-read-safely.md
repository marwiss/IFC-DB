# ADR 0013: IDS is checked with IfcTester, and read safely

*Recorded in IFC-LPG. Its number follows Kontroll's, whose decisions 0009 to 0012 this repository has, so that a number
names one decision in both.*

Status: accepted (2026-11-07)

## Context

buildingSMART's Information Delivery Specification, IDS 1.0, says in XML what a model must hold: which elements, with
which attributes, properties, classifications, materials and parts. Kontroll's owner decided that the server checks
uploaded IFC files against IDS as well, besides the IFC schema (ADR 0011) and buildingSMART's rules (ADR 0012), and that
the check is built here. Dataspatium, the client that goes with Kontroll, already checks a model against an IDS in the
browser, with IfcOpenShell's IfcTester 0.9.0 and its defaults.

An IDS comes from a person. IfcTester reads one with the standard library's parser and with xmlschema, and neither refuses
entities. Its schema, `ids.xsd` as IfcTester ships it, imports `xml.xsd`, `XMLSchema.xsd` and `XMLSchema-instance` by
their addresses at w3.org. Built as IfcTester builds it, with xmlschema's default `allow="all"`, the schema reaches for
www.w3.org on port 80 three times, over plain HTTP, waiting up to 300 s for each. A probe on 2026-11-07, with the network
blocked in its process, saw the three attempts. Built with `allow="local"`, the same schema comes from xmlschema's own
copies of the three, with no attempt.

IfcTester 0.9.0 also leaves out a specification's `identifier`, which IDS 1.0 has. `Specification.parse` reads the name,
description, instructions, occurrences, IFC versions and facets, but not the identifier.

## Decision

**IfcTester checks, with its own defaults.** No IDS checker of our own: IfcTester is IfcOpenShell's, under the
LGPL-3.0-or-later, and the one Dataspatium runs, so the server and the client give the same verdict on the same model and
IDS. That holds for IFC versions too. A specification is checked whatever versions it names, as `Ids.validate` does by
default, and its summary says whether it names the file's (`for_this_schema`). Filtering by version would be a reading of
IDS 1.0 of our own, and the server and the client would then disagree.

**An IDS is read here, not by IfcTester.** `ifc_lpg.ids.read_ids` takes the bytes, and:

- refuses more than 20 MB;
- parses them with defusedxml and `forbid_dtd=True`, so that no entity is ever declared, inside the file or outside it;
- validates the tree against IDS 1.0's schema, built once with `allow="local"` and handed to IfcTester as its own, so
  that IfcTester never builds the other;
- decodes the tree with that schema, without location hints, as xmlschema does by default, and gives the result to
  IfcTester's `Ids.parse`;
- reads the identifiers IfcTester leaves out, in the same order.

An IDS that fails any of this is listed under `rules_not_run`, with the reason, and the other IDS files are still checked.

**In a process of its own.** `python -m ifc_lpg.ids FILE IDS...`, or `ifc_lpg.files.check_ids`, runs with a memory limit
on POSIX and the caller's time limit, as the other checks do. An IDS can hold patterns in XML Schema's regular
expressions, which IfcTester applies to the file's values. One that takes too long ends that process, not the caller.

**The graph side needs none of it.** IfcOpenShell, IfcTester, defusedxml and the pytest that IfcOpenShell's EXPRESS
rules import come with the extra `ifcopenshell`, and the package itself requires nothing. Every module imports them
where it uses them, in the workers, so that a program can import, export and delete models and read the packed reports
without them. Kontroll does that in its own process, and its slim image has no IfcOpenShell.

**Every failure is an error.** IDS 1.0 has no severity. An issue is a requirement that an element fails, a required
specification that applies to nothing, or an element that a prohibited specification applies to. Each has the
specification's identifier, or else its name, as its rule, the index of its IDS, and the element's STEP id and GlobalId
where there is one. The report has the shape of the schema check's (ADR 0011) and the rules' (ADR 0012), with a summary
of each specification: its usage, its verdict, how many elements it applies to and how many issues it gave. The verdict
is `passed`, `failed`, or `not applicable` for an optional specification that applies to nothing, which IfcTester's own
reports mark as skipped.

## Consequences

- IfcTester requires what its reports, its BCF output and its web app use: Flask with Werkzeug, Jinja2, Click, Blinker
  and itsdangerous; bcf-client with xsdata; odfpy; pystache. The check itself uses xmlschema and elementpath. This
  package imports none of the others, but they are installed wherever the extra is.
- defusedxml is in the extra in its own right, not because it happens to come with odfpy.
- The install command names the extra, `ifc-lpg[ifcopenshell,neo4j]`, for the round trip of files.
- If IfcTester comes to read identifiers, or to build its schema without the network, the code here that does it becomes
  redundant but stays harmless, and can then go.
- The check is as good as IfcTester's reading of IDS 1.0. Where that differs from buildingSMART's, it differs for
  Dataspatium too, and the two still agree.
