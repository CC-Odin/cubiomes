// Builds the overworld biome R-tree with Minecraft's own Climate.RTree code from
// a biome_parameters report and dumps it in pre-order:
//   N <children> <min max> x7      (inner node)
//   L <biome>    <min max> x7      (leaf)
// usage: DumpTree REPORT OUT [CHILDREN_PER_NODE]
//
// Generating a biome tree table for a new version:
//  1. Get the report from the server jar of that version:
//       java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --reports
//     -> generated/reports/biome_parameters/minecraft/overworld.json
//  2. Compile and run this against an unobfuscated server jar (26.1+) and the
//     libraries that the bundled jar extracts (versions/ and libraries/):
//       javac -cp "$CP" DumpTree.java && java -cp "$CP:." DumpTree overworld.json tree.txt 19
//     CHILDREN_PER_NODE is 6 up to 26.2 and 19 since 26.3 (Climate.RTree).
//  3. python3 nptree_encode.py tree.txt ../biomes.h btreeXX "<version>" ../tables/btreeXX.h
// Regenerating 1.21.4 this way reproduces tables/btree21wd.h byte for byte.
import com.google.gson.*;
import com.mojang.datafixers.util.Pair;
import net.minecraft.world.level.biome.Climate;

import java.io.*;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

public class DumpTree
{
    static final String[] KEYS = {"temperature","humidity","continentalness","erosion","depth","weirdness"};

    static Climate.Parameter param(JsonElement e)
    {
        if (e.isJsonArray())
            return Climate.Parameter.span(e.getAsJsonArray().get(0).getAsFloat(), e.getAsJsonArray().get(1).getAsFloat());
        return Climate.Parameter.point(e.getAsFloat());
    }

    static Field field(Class<?> c, String n) throws Exception
    {
        Field f = c.getDeclaredField(n);
        f.setAccessible(true);
        return f;
    }

    public static void main(String[] args) throws Exception
    {
        JsonObject root = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
        List<Pair<Climate.ParameterPoint, String>> list = new ArrayList<>();
        for (JsonElement el : root.getAsJsonArray("biomes"))
        {
            JsonObject o = el.getAsJsonObject();
            JsonObject p = o.getAsJsonObject("parameters");
            Climate.Parameter[] ps = new Climate.Parameter[6];
            for (int i = 0; i < 6; i++)
                ps[i] = param(p.get(KEYS[i]));
            Climate.ParameterPoint pt = Climate.parameters(ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], p.get("offset").getAsFloat());
            list.add(Pair.of(pt, o.get("biome").getAsString().replace("minecraft:", "")));
        }

        Climate.ParameterList<String> plist = new Climate.ParameterList<>(list);
        if (args.length > 2)
            plist = plist.rebuildWithChildrenPerNode(Integer.parseInt(args[2]));
        Object tree = field(Climate.ParameterList.class, "index").get(plist);
        Class<?> node = Class.forName("net.minecraft.world.level.biome.Climate$RTree$Node");
        Class<?> sub  = Class.forName("net.minecraft.world.level.biome.Climate$RTree$SubTree");
        Class<?> leaf = Class.forName("net.minecraft.world.level.biome.Climate$RTree$Leaf");
        Field fRoot = field(Class.forName("net.minecraft.world.level.biome.Climate$RTree"), "root");
        Field fSpace = field(node, "parameterSpace");
        Field fChildren = field(sub, "children");
        Field fValue = field(leaf, "value");

        PrintWriter out = new PrintWriter(new BufferedWriter(new FileWriter(args[1])));
        Deque<Object> stack = new ArrayDeque<>();
        stack.push(fRoot.get(tree));
        while (!stack.isEmpty())
        {
            Object n = stack.pop();
            StringBuilder sb = new StringBuilder();
            if (sub.isInstance(n))
            {
                Object[] ch = (Object[]) fChildren.get(n);
                sb.append("N ").append(ch.length);
                for (int i = ch.length - 1; i >= 0; i--)
                    stack.push(ch[i]);
            }
            else
            {
                sb.append("L ").append(fValue.get(n));
            }
            for (Climate.Parameter p : (Climate.Parameter[]) fSpace.get(n))
                sb.append(' ').append(p.min()).append(' ').append(p.max());
            out.println(sb);
        }
        out.close();
    }
}
