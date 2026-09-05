# Review fixture

`parse_ratio` accepts a `left:right` string. The existing test covers an
ordinary ratio but does not describe the zero denominator behavior. A review
should identify the edge case and propose a regression test without changing
the fixture.
