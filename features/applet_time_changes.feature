Feature: Clock and calendar icons follow changes in local time
  Scenario: Calendar changes weekday while the day number stays the same
    Given a calendar showing Thursday January 15
    When local time changes to Sunday February 15
    Then the calendar icon shows the new weekday and its tooltip shows February

  Scenario Outline: Clock follows changes outside the minute field
    Given a digital clock showing 2026-01-15 10:30 with its date
    When the clock's local time changes to <later>
    Then the clock icon and tooltip show the new local time

    Examples:
      | later            |
      | 2026-01-15 11:30 |
      | 2026-01-16 10:30 |
