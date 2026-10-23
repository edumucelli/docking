Feature: Backend-aware dock pointer crossings
  Native Wayland leave events must not depend on global pointer coordinates.
  X11 shape crossings must retain hover when the pointer is still inside.

  Scenario: Native Wayland exit hides despite stale inside coordinates
    Given a hovered "Wayland" dock whose pointer query reports "inside"
    When the dock receives a "NONLINEAR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is hidden
    And no global dock pointer query was made

  Scenario: Moving into a native child does not hide the dock
    Given a hovered "Wayland" dock whose pointer query reports "inside"
    When the dock receives a "INFERIOR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is visible
    And no global dock pointer query was made

  Scenario: X11 shape crossing at the left edge retains hover
    Given a hovered "X11" dock whose pointer query reports "inside"
    When the dock receives a "ANCESTOR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is visible

  Scenario: X11 genuine exit hides despite stale crossing coordinates
    Given a hovered "X11" dock whose pointer query reports "outside"
    When the dock receives a "NONLINEAR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is hidden

  Scenario: A native menu grab keeps the dock open until the menu closes
    Given a hovered "Wayland" dock whose pointer query reports "inside"
    And a dock menu holds autohide open
    When the dock receives a "NONLINEAR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is visible
    When the dock menu closes with the pointer outside
    And I advance dock time by 96 milliseconds
    Then the dock is hidden

  Scenario: Native leave defers hiding during preview handoff
    Given a hovered "Wayland" dock whose pointer query reports "inside"
    And a preview is visible during the crossing
    When the dock receives a "NONLINEAR" leave crossing
    And I advance dock time by 96 milliseconds
    Then the dock is visible
    And preview dismissal is scheduled
    And no global dock pointer query was made
