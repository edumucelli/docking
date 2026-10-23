Feature: Load only visible folder stack thumbnails
  Scenario Outline: Sort before selecting visible thumbnails
    Given a thumbnail folder containing 30 files
    When its stack is built using "<sort>" sorting
    Then nine sorted stack icons and the remaining file count are available
    When its full directory menu is opened twice
    Then all 30 menu icons are loaded only once

    Examples:
      | sort     |
      | name     |
      | kind     |
      | size     |
      | created  |
      | modified |
