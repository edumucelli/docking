Feature: Compositor-constrained dock allocation
  A layer-shell dock uses its assigned space without changing the saved icon size.

  Scenario Outline: Panel creation, growth and removal keep icons reachable
    Given a crowded layer-shell dock at the "<edge>" edge
    When the compositor reserves 64 pixels along the dock's length
    Then every icon fits and its painted center is clickable
    When the compositor reserves 112 pixels along the dock's length
    Then every icon fits and its painted center is clickable
    When the compositor reserves 0 pixels along the dock's length
    Then every icon fits and its painted center is clickable
    And the preferred icon size is restored

    Examples:
      | edge   |
      | bottom |
      | top    |
      | left   |
      | right  |

  Scenario: Switching axes clears the previous GTK minimum
    Given a crowded layer-shell dock at the "bottom" edge
    When the layer-shell dock moves to the "right" edge
    And the compositor reserves 112 pixels along the dock's length
    Then every icon fits and its painted center is clickable
    When the layer-shell dock moves to the "top" edge
    And the compositor reserves 112 pixels along the dock's length
    Then every icon fits and its painted center is clickable
