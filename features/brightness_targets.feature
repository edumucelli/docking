Feature: Brightness reflects the selected display hardware
  Scenario Outline: Initial brightness uses the correct backend and output
    Given brightness controls for a "<setup>" display setup
    Then the brightness tooltip and icon show <percent> percent

    Examples:
      | setup                | percent |
      | native Wayland       | 60      |
      | secondary primary    | 75      |
      | similar output names | 75      |
