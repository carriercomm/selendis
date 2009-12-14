get_player = function() {
    $.getJSON("/api/players/me.json", function(player) {
        document.player = player;
    });
}

/* modifiers */

modify_player = function(data) {
    $.ajax({
        type: "PUT",
        url: "/api/players/me.json",
        data: data,
        dataType: "json",
        success: function(player) {
            document.player = player
            render_profile();
        }
    });
}

render_profile = function() {
    $("#player_name").html(document.player.name);
    $("#player_level").html(document.player.level);
    $("#hp").html(document.player.hp + ' / ' + document.player.max_hp);    
    $("#mp").html(document.player.mp + ' / ' + document.player.max_mp);

    // main hand
    var main_hand = null;
    if (document.player.main_hand) {
        main_hand = document.player.main_hand.name;
    } else {
        main_hand = "&lt;empty&gt;";
    }
    $("#player_main_hand").html(main_hand);

    $("#player_inventory").empty();
    $.each(document.player.items, function() {
        var line = $("<div></div>");
        line.append(item_link(this));

        line.appendTo("#player_inventory");
    });
}