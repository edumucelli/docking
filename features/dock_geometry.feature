Feature: Dock geometry at every screen edge
  Painted content, input targets, and animation bounds must share one geometry.

  Scenario Outline: A floating gap aligns painting and input
    Given the dock is at the "<position>" edge with a 30 pixel floating gap
    When I render the resting dock geometry
    Then the painted icon matches its input target
    And the floating gap does not target the icon
    And the painted shelf is 30 pixels from the screen edge

    Examples:
      | position |
      | bottom   |
      | top      |
      | left     |
      | right    |

  Scenario Outline: Peak animation stays inside the dock surface
    Given the dock is at the "<position>" edge with a 30 pixel floating gap
    When launch and urgency bounces peak at maximum zoom
    Then the painted icon stays inside the dock surface

    Examples:
      | position |
      | bottom   |
      | top      |
      | left     |
      | right    |

  Scenario Outline: Changing dock axis snaps icons to the new shelf
    Given the dock has rendered at the "<old_position>" edge
    When I move the dock to the "<new_position>" edge
    Then the icons immediately align with the new shelf

    Examples:
      | old_position | new_position |
      | bottom       | right        |
      | right        | top          |
      | top          | left         |
      | left         | bottom       |

  Scenario: Changing dock side moves the input shape to the visible shelf
    Given the dock has rendered at the "right" edge
    When I move the dock to the "left" edge
    Then the left screen edge remains inside the dock input

  Scenario: X11 input reshaping does not release the left screen edge
    Given the pointer is held at the left screen edge
    When X11 reports a shape crossing outside the left edge
    Then the dock remains hovered
