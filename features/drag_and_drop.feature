Feature: Drag and drop
  Drag interactions should preserve the intended reorder and external pinning flows.

  Scenario: Dragging a dock item across the insertion boundary reorders it
    Given a drag can start from the "a.desktop" dock item
    When I begin dragging the "a.desktop" dock item
    And I drag to insertion index 2
    Then the drag reorder is applied

  Scenario: Dropping an external launcher pins it at the requested slot
    When I drop the launcher URI "file:///usr/share/applications/firefox.desktop" at insertion index 0
    Then the pinned targets include "firefox.desktop"

  Scenario: Dragging a pinned item away from the dock removes it
    Given a drag can start from the "a.desktop" dock item
    When I drag the "a.desktop" dock item outside the dock and release it
    Then the "a.desktop" dock item is unpinned
