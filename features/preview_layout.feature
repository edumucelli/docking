@gtk_preview
Feature: Preview popup layout
  Window previews remain reachable and correctly sized on every dock edge.
  These scenarios allocate real GTK widgets, including asynchronous layout.

  Scenario Outline: Preview groups fit the available monitor
    When I open <count> window previews at the <edge> edge
    Then the entire preview popup fits its monitor workarea
    And the preview contains <count> window cards

    Examples:
      | edge   | count |
      | bottom | 1     |
      | bottom | 2     |
      | bottom | 8     |
      | top    | 1     |
      | top    | 2     |
      | top    | 8     |
      | left   | 1     |
      | left   | 2     |
      | left   | 8     |
      | right  | 1     |
      | right  | 2     |
      | right  | 8     |

  Scenario Outline: Reusing a scrolled popup restores its natural size
    When I open 1 window previews at the <edge> edge
    And I remember the preview size
    And I open 8 window previews at the <edge> edge
    And I scroll to the last preview at the <edge> edge
    And I open 1 window previews at the <edge> edge
    Then the preview returns to its remembered size
    And the preview scroll position is reset

    Examples:
      | edge   |
      | bottom |
      | top    |
      | left   |
      | right  |

  Scenario Outline: Both ends of an overflowing group can be activated
    When I open 8 window previews at the <edge> edge
    And I scroll to the last preview at the <edge> edge
    Then I can activate preview card 8
    When I open 8 window previews at the <edge> edge
    And I scroll to the first preview at the <edge> edge
    Then I can activate preview card 1

    Examples:
      | edge   |
      | bottom |
      | top    |
      | left   |
      | right  |

  Scenario Outline: A normal mouse wheel navigates horizontal previews
    When I open 8 window previews at the <edge> edge
    And I use the mouse wheel over the preview
    Then the preview has scrolled horizontally

    Examples:
      | edge   |
      | bottom |
      | top    |
