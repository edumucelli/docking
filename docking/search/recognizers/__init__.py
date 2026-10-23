"""Pure, conservative recognizers for structured global search queries.

Recognizers have no GTK dependency and perform no external side effects. They
return immutable semantic values only when syntax is sufficiently clear to
route away from ordinary fuzzy search. A provider may then present that value
and own its actions without reimplementing parsing policy.
"""
