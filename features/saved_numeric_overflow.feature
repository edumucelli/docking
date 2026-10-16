@native_services
Feature: Applets recover saved JSON numbers outside the floating point range
  Scenario Outline: An overflowing saved number uses the default preference
    Given the "<applet>" applet has "<key>" saved as an overflowing JSON number
    When that applet is constructed from the saved file
    Then its icon and tooltip are available

    Examples:
      | applet       | key                    |
      | crypto       | active_index           |
      | currencyfx   | active_index           |
      | lastfm       | max_entries            |
      | news         | active_article_index   |
      | news         | active_source_index    |
      | reddit       | active_post_index      |
      | reddit       | active_subreddit_index |
      | stretchcoach | interval               |
