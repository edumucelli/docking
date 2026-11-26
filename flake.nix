{
  description = "Docking desktop dock";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { nixpkgs, ... }: {
    packages = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system:
      let
        docking = import ./packaging/nix/default.nix {
          pkgs = nixpkgs.legacyPackages.${system};
        };
      in
      {
        inherit docking;
        default = docking;
      });
  };
}
