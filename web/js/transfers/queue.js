function initPage()
{
        var ajaxInput = {
        'url': dataPath + '/transfers/queue',
        'success': function (data, textStatus, jqXHR) { drawGraph(data.data); },
        'error': handleError,
        'dataType': 'json',
        'async': false

    };

    $.ajax(ajaxInput);
    
    drawGraph();
}

function drawGraph(source)
{
    var trace1 = {
        x: ['giraffes', 'orangutans', 'monkeys'],
        y: [20, 14, 23],
        name: 'SF Zoo',
        type: 'bar'
    };
    
    var trace2 = {
        x: ['giraffes', 'orangutans', 'monkeys'],
        y: [12, 18, 29],
        name: 'LA Zoo',
        type: 'bar'
    };
    
    var data = [trace1, trace2];
    
    var layout = {barmode: 'stack'};
    
    Plotly.newPlot('graph', data, layout);
}
